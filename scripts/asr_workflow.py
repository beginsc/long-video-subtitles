"""Persistent ASR selection: local WhisperX, remote MAI-Transcribe-2, custom adapters.

Python 3.9+. Selection is independent of video download preferences. Each call
processes one timestamped audio block, preserves raw evidence and never retries POST.
"""
import argparse
import copy
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid
from urllib.parse import urlsplit

import media_intake as io
from asr_types import row, structural

ROOT = Path(__file__).resolve().parent.parent
MODEL = 'MAI-Transcribe-2'
ALIASES = {'local': 'whisperx', 'remote': 'mai-transcribe-2'}
BUILTINS = ('whisperx', 'mai-transcribe-2')


def name(value):
    return ALIASES.get(value, io.backend_name(value))


def config_path(value=None):
    if value or os.environ.get('LVS_ASR_CONFIG'):
        return Path(value or os.environ['LVS_ASR_CONFIG']).expanduser().resolve()
    base = (Path(os.environ.get('APPDATA', Path.home() / 'AppData' / 'Roaming'))
            if os.name == 'nt' else Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')))
    return base / 'long-video-subtitles' / 'asr.json'


def adapter(value):
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise io.IntakeError('ASR adapter requires schema_version=1.')
    command = value.get('command')
    if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x for x in command):
        raise io.IntakeError('ASR adapter command must be an argument array.')
    joined = '\n'.join(command)
    for field in ('{request_json}', '{output_dir}', '{result_json}'):
        if field not in joined:
            raise io.IntakeError('ASR adapter requires request_json, output_dir and result_json placeholders.')
    if set(re.findall(r'\{([a-z_]+)\}', joined)) - {'request_json', 'output_dir', 'result_json', 'python'}:
        raise io.IntakeError('Unknown ASR adapter placeholder.')
    required = value.get('required_env', [])
    if not isinstance(required, list) or any(not isinstance(x, str) or not re.fullmatch('[A-Za-z_][A-Za-z_0-9]*', x) for x in required):
        raise io.IntakeError('required_env contains environment names only.')
    io.positive(value.get('timeout_seconds', 3600))
    return value


def endpoint_info(value):
    """Resolve the official Foundry project URL to its SAME resource host.

    The official MAI notebook removes /api/projects/<project>. It does not
    change a services.ai.azure.com host into cognitiveservices.azure.com.
    """
    u = urlsplit(value)
    host = u.hostname or ''
    if (u.scheme != 'https' or u.username or u.password or u.port not in (None, 443)
            or u.query or u.fragment):
        raise io.IntakeError('Use an official HTTPS Foundry/Speech endpoint without query, credentials or fragment.')
    if re.fullmatch(r'[a-z0-9-]+\.openai\.azure\.com', host):
        raise io.IntakeError('Azure OpenAI endpoint: use the Foundry project endpoint or its AIServices/Speech endpoint and matching resource key; do not rename the host.')
    if re.fullmatch(r'[a-z0-9-]+\.services\.ai\.azure\.com', host):
        if u.path in ('', '/'):
            kind = 'foundry_resource'
        elif re.fullmatch(r'/api/projects/[A-Za-z0-9][A-Za-z0-9._-]*/?', u.path):
            kind = 'foundry_project'
        else:
            raise io.IntakeError('Foundry URL must be its resource root or /api/projects/<project>.')
    elif (re.fullmatch(r'[a-z0-9-]+\.cognitiveservices\.azure\.com', host)
          or re.fullmatch(r'[a-z0-9-]+\.api\.cognitive\.microsoft\.com', host)) and u.path in ('', '/'):
        kind = 'speech_resource'
    else:
        raise io.IntakeError('Use an official Foundry project/resource or Speech root; third-party endpoints use a custom adapter.')
    return {'endpoint': f'https://{host}', 'configured_endpoint': value.rstrip('/'), 'endpoint_kind': kind}


def endpoint(value):
    return endpoint_info(value)['endpoint']


def speech_region(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9]{1,39}', value):
        raise io.IntakeError('Use an Azure region identifier, for example southeastasia; no default region is assumed.')
    return value


def key_env_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', value):
        raise io.IntakeError('key-env must be an environment variable NAME, never the key value.')
    return value


def mai_spec(cfg):
    """Choose an endpoint and its credential family together, never mix env families."""
    prefs = cfg.get('mai', {})
    speech_keys = ['AZURE_SPEECH_KEY', 'AZURE_SPEECH_API_KEY']
    selected_endpoint, selected_region = prefs.get('endpoint'), prefs.get('region')
    endpoint_source = None
    if selected_endpoint:
        info = endpoint_info(selected_endpoint)
        keys = ['MICROSOFT_FOUNDRY_API_KEY'] if info['endpoint_kind'].startswith('foundry_') else speech_keys
        endpoint_source = 'config.endpoint'
    elif selected_region:
        info = endpoint_info(f'https://{speech_region(selected_region)}.api.cognitive.microsoft.com')
        # Preserve pre-S14.2 saved region configs without a key binding.
        # New --region configurations explicitly bind MAI_SPEECH_KEY below.
        keys = speech_keys + ['MAI_SPEECH_KEY']
        endpoint_source = 'config.region'
    else:
        # Region + key is the default for unbound configurations. An explicit
        # saved address/region above still takes precedence over environment.
        families = [('MAI_SPEECH_REGION', True, ['MAI_SPEECH_KEY']),
                    ('AZURE_SPEECH_REGION', True, speech_keys),
                    ('AZURE_SPEECH_ENDPOINT', False, speech_keys),
                    ('MICROSOFT_FOUNDRY_ENDPOINT', False, ['MICROSOFT_FOUNDRY_API_KEY'])]
        info, keys = {'endpoint': None, 'configured_endpoint': None, 'endpoint_kind': None}, ['MAI_SPEECH_KEY']
        for variable, is_region, family_keys in families:
            value = os.environ.get(variable)
            if not value:
                continue
            selected_region = speech_region(value) if is_region else None
            info = endpoint_info(f'https://{selected_region}.api.cognitive.microsoft.com' if is_region else value)
            keys, endpoint_source = family_keys, variable
            break
    key_env = (key_env_name(prefs['key_env']) if prefs.get('key_env') else
               next((key for key in keys if os.environ.get(key)), keys[0]))
    return {'model': MODEL, 'api_version': '2025-10-15', 'key_env': key_env,
            'endpoint_source': endpoint_source, 'region': selected_region, **info}


def registry(path):
    if not path.exists():
        return None
    cfg = io.load(path)
    if not isinstance(cfg, dict) or cfg.get('schema_version') != 1:
        raise io.IntakeError('Unsupported ASR preference schema.')
    selected = name(cfg.get('backend'))
    custom = cfg.get('custom_backends', {})
    if not isinstance(custom, dict):
        raise io.IntakeError('custom_backends must be an object.')
    for key, definition in custom.items():
        if name(key) in BUILTINS or name(key) != key:
            raise io.IntakeError('Custom ASR ID collides with a built-in name.')
        adapter(definition)
    if selected not in BUILTINS and selected not in custom:
        raise io.IntakeError('Selected ASR adapter is not registered.')
    if cfg.get('mai', {}).get('endpoint'):
        endpoint(cfg['mai']['endpoint'])
    if cfg.get('mai', {}).get('region'):
        speech_region(cfg['mai']['region'])
    if cfg.get('mai', {}).get('endpoint') and cfg.get('mai', {}).get('region'):
        raise io.IntakeError('Choose one configured endpoint or region, not both.')
    if cfg.get('mai', {}).get('key_env'):
        key_env_name(cfg['mai']['key_env'])
    return cfg


def configure(args):
    path = config_path(args.config)
    cfg = registry(path) or {'schema_version': 1, 'custom_backends': {}}
    selected = args.backend
    if not selected:
        if not sys.stdin.isatty():
            raise io.IntakeError('Choose --backend local, remote, or a custom ASR ID; no upload or selection made.')
        print('1. 本地 WhisperX：音频留在本机，需要单独安装环境和模型。')
        print('2. 远程 MAI-Transcribe-2：默认使用区域＋密钥，也支持 Foundry/Speech 地址；上传音频且可能计费。')
        print('3. 自定义：添加第三方服务或本机转写程序。')
        print('已注册：' + ', '.join(cfg['custom_backends']))
        selected = input('选择 [1/2/3 或已注册 ID]: ').strip()
        selected = {'1': 'whisperx', '2': 'mai-transcribe-2'}.get(selected, selected)
        if selected == '3':
            selected = input('自定义 ASR ID: ').strip()
            args.adapter = input('适配定义 JSON 路径（已注册可留空）: ').strip() or None
    selected = name(selected)
    if args.adapter:
        if selected in BUILTINS:
            raise io.IntakeError('--adapter requires a custom ASR ID.')
        cfg['custom_backends'][selected] = adapter(io.load(Path(args.adapter).expanduser()))
    if selected not in BUILTINS and selected not in cfg['custom_backends']:
        raise io.IntakeError('Register the custom ASR with --adapter <definition.json>.')
    if args.endpoint:
        info = endpoint_info(args.endpoint)
        prefs = cfg.setdefault('mai', {})
        prefs['endpoint'] = info['configured_endpoint']
        prefs.pop('region', None)
        # A changed address must not inherit an explicit key from the old resource.
        prefs.pop('key_env', None)
    if args.region:
        prefs = cfg.setdefault('mai', {})
        prefs['region'] = speech_region(args.region)
        prefs.pop('endpoint', None)
        prefs['key_env'] = 'MAI_SPEECH_KEY'
    if args.key_env:
        cfg.setdefault('mai', {})['key_env'] = key_env_name(args.key_env)
    if args.whisperx_executable:
        cfg.setdefault('whisperx', {})['executable'] = args.whisperx_executable
    if args.runtime_root:
        cfg.setdefault('whisperx', {})['runtime_root'] = str(Path(args.runtime_root).expanduser().resolve())
    if args.local_driver:
        cfg.setdefault('whisperx', {})['driver'] = args.local_driver
    cfg.update(backend=selected, configured_at=io.now())
    io.save(path, cfg, replace=True)
    return {'status': 'configured', 'backend': selected, 'config_path': str(path),
            'custom_backends': list(cfg['custom_backends']), 'network_tested': False}


def choose(args):
    cfg = registry(config_path(args.config)) or {}
    selected = args.backend or cfg.get('backend')
    if not selected:
        raise io.IntakeError('ASR preference missing; configure local, remote or a custom backend first.')
    selected = name(selected)
    if selected == 'whisperx':
        spec = {'driver': 'cli', 'model': 'large-v3', **cfg.get('whisperx', {})}
        if spec['driver'] == 'runtime':
            # Explicit runtime selection may reuse this machine's legacy installation.
            root = spec.get('runtime_root')
            legacy = ROOT / 'assets' / 'whisperx-local.json'
            if not root and legacy.exists():
                root = io.load(legacy).get('runtime_root')
            if not root:
                raise io.IntakeError('Configure --runtime-root for the compatible local WhisperX runner.')
            spec['runtime_root'] = str(Path(root).expanduser().resolve())
        elif spec['driver'] != 'cli':
            raise io.IntakeError('Unsupported WhisperX local driver.')
    elif selected == 'mai-transcribe-2':
        spec = mai_spec(cfg)
    else:
        spec = cfg.get('custom_backends', {}).get(selected)
        if spec is None:
            raise io.IntakeError('Unknown ASR backend; register its adapter first.')
        adapter(spec)
    return selected, copy.deepcopy(spec)


def local_command(spec):
    if spec['driver'] == 'runtime':
        root = Path(spec['runtime_root'])
        python = root / ('venv/Scripts/python.exe' if os.name == 'nt' else 'venv/bin/python')
        runner = root / 'run_local.py'
        if not python.is_file() or not runner.is_file():
            raise io.IntakeError('Compatible WhisperX runtime is missing python or run_local.py.')
        return [str(python), '-B', '-X', 'utf8', str(runner)]
    return [io.binary(spec.get('executable', 'whisperx'))]


def runtime_side_effects(spec):
    if spec.get('driver') != 'runtime':
        return []
    root = Path(spec['runtime_root']).expanduser().resolve()
    return [{'path': str(root/name), 'operations': ['create', 'write'],
             'purpose': purpose, 'tool': 'compatible WhisperX run_local.py',
             'scope': 'tool-managed files only; verify installed runner; not a permission grant'}
            for name, purpose in [('tmp', 'temporary decoding and runtime files'),
                                   ('cache', 'existing runtime library/model caches')]]


def check(args):
    selected, spec = choose(args)
    result = {'backend': selected, 'config_path': str(config_path(args.config)), 'network_tested': False,
              'ffprobe_available': bool(shutil.which(args.ffprobe))}
    if selected == 'mai-transcribe-2':
        result.update(model=MODEL, endpoint_configured=bool(spec['endpoint']),
                      endpoint=spec['endpoint'], configured_endpoint=spec['configured_endpoint'],
                      endpoint_kind=spec['endpoint_kind'], endpoint_source=spec['endpoint_source'],
                      key_env=spec['key_env'], key_present=bool(os.environ.get(spec['key_env'])),
                      curl_available=bool(shutil.which('curl')))
    elif selected == 'whisperx':
        result['tool_side_effects'] = runtime_side_effects(spec)
        try:
            result['command'] = local_command(spec)
            result['driver'] = spec['driver']
            result['models_verified'] = False
        except io.IntakeError as e:
            result['dependency_error'] = str(e)
    else:
        result.update(command_available=bool(shutil.which(spec['command'][0].replace('{python}', sys.executable))),
                      missing_env=[n for n in spec.get('required_env', []) if not os.environ.get(n)])
    return result


def number(value, label):
    if type(value) not in (float, int) or not math.isfinite(value):
        raise io.IntakeError('Invalid ' + label)
    return value


def normalize(raw, request):
    """Canonical segment/word seconds stay relative to the input block."""
    rows = []
    duration = request['media']['duration_ms'] / 1000
    offset = request['offset_ms']
    if not isinstance(raw, dict):
        raise io.IntakeError('ASR canonical output must be an object.')
    segments = raw.get('segments')
    if not isinstance(segments, list):
        raise io.IntakeError('ASR output must have a segments list with real timestamps.')
    for segment in segments:
        if not isinstance(segment, dict):
            raise io.IntakeError('Invalid ASR segment.')
        a = number(segment.get('start'), 'segment start')
        b = number(segment.get('end'), 'segment end')
        if not 0 <= a < b <= duration + .1 or not isinstance(segment.get('text'), str) or not segment['text'].strip():
            raise io.IntakeError('Invalid segment interval or empty transcript; raw result retained.')
        item = row(f's{len(rows)+1:06d}', round(a*1000)+offset, round(b*1000)+offset, segment['text'])
        words = copy.deepcopy(segment.get('words', []))
        if not isinstance(words, list) or any(not isinstance(w, dict) for w in words):
            raise io.IntakeError('Invalid word evidence.')
        missing = 0
        for word in words:
            if word.get('start') is None or word.get('end') is None:
                missing += 1
                continue
            start = number(word['start'], 'word start'); end = number(word['end'], 'word end')
            if not 0 <= start <= end <= duration + .1:
                raise io.IntakeError('Invalid word interval.')
        item.update(asr_words=words, asr_words_offset_ms=offset,
                    timing_precision=('segment_only' if not words else 'word_partial' if missing else 'word'))
        if missing or not words:
            item['issues'].append('Word timing missing or incomplete; do not invent character timing.')
        for key in ('speaker', 'confidence', 'locale'):
            if key in segment:
                item['asr_' + key if key != 'speaker' else key] = segment[key]
        rows.append(item)
    result = {'schema_version': 1, 'meta': {'asr_backend': request['backend'],
              'asr_model_requested': request['spec'].get('model'), 'input_sha256': request['media']['sha256'],
              'source_language': raw.get('language'), 'offset_ms': offset,
              'asr_audio_duration_seconds': duration, 'evidence_status': 'asr_output_not_listened'}, 'segments': rows}
    if rows:
        structural(result)
    return result


def mai_canonical(raw):
    phrases = raw.get('phrases')
    if not isinstance(phrases, list):
        raise io.IntakeError('MAI response has no timed phrases; retain raw response for alignment.')
    if not phrases and any(p.get('text', '').strip() for p in raw.get('combinedPhrases', [])):
        raise io.IntakeError('MAI returned text without timed phrases; separate alignment required.')
    segments = []
    for phrase in phrases:
        start = number(phrase.get('offsetMilliseconds'), 'MAI phrase offset')
        duration = number(phrase.get('durationMilliseconds'), 'MAI phrase duration')
        segment = {'start': start/1000, 'end': (start+duration)/1000, 'text': phrase['text'], 'words': []}
        for key in ('speaker', 'locale', 'confidence'):
            if key in phrase:
                segment[key] = phrase[key]
        for word in phrase.get('words', []):
            item = {'word': word['text']}
            if word.get('offsetMilliseconds') is not None and word.get('durationMilliseconds') is not None:
                w = number(word['offsetMilliseconds'], 'MAI word offset')
                d = number(word['durationMilliseconds'], 'MAI word duration')
                item.update(start=w/1000, end=(w+d)/1000)
            segment['words'].append(item)
        segments.append(segment)
    return {'segments': segments, 'language': None}


def mai_definition(request):
    # Model options placement follows the model's official REST request example.
    result = {'enhancedMode': {'enabled': True, 'model': MODEL,
              'modelOptions': {'timestamps': 'word', 'transcribeStyle': 'verbatim'}}}
    if request.get('language'):
        result['locales'] = [request['language']]
    return result


def transcribe_mai(request, out):
    spec = request['spec']; key = os.environ[spec['key_env']]
    boundary = 'lvs-' + uuid.uuid4().hex
    source = Path(request['media']['path'])
    body = out / 'upload.multipart.private'
    definition = mai_definition(request)
    io.save(out / 'mai-definition.json', definition)
    with body.open('xb') as target:
        target.write((f'--{boundary}\r\nContent-Disposition: form-data; name="definition"\r\nContent-Type: application/json\r\n\r\n'
                      + json.dumps(definition) + '\r\n').encode())
        audio_type = {'.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.flac': 'audio/flac'}[source.suffix.lower()]
        target.write((f'--{boundary}\r\nContent-Disposition: form-data; name="audio"; filename="audio{source.suffix.lower()}"\r\n'
                      f'Content-Type: {audio_type}\r\n\r\n').encode())
        with source.open('rb') as audio:
            shutil.copyfileobj(audio, target, 1024*1024)
        target.write(f'\r\n--{boundary}--\r\n'.encode())
    def quote(text):
        if any(c in text for c in ('\n', '\r', '\x00')):
            raise io.IntakeError('Invalid authentication header.')
        return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'
    url = endpoint(spec['endpoint']) + '/speechtotext/transcriptions:transcribe?api-version=2025-10-15'
    config = 'url = ' + quote(url) + '\nheader = ' + quote('Ocp-Apim-Subscription-Key: ' + key) + '\n'
    partial = out / 'mai-response.partial.private.json'
    command = [io.binary('curl'), '--disable', '--config', '-', '--silent', '--show-error', '--retry', '0',
               '--proto', '=https', '--connect-timeout', '30', '--max-time', '900',
               '--header', 'Content-Type: multipart/form-data; boundary=' + boundary,
               '--data-binary', '@' + str(body), '--output', str(partial), '--write-out', '%{http_code}']
    io.save(out / 'submit-intent.json', {'endpoint': url, 'input_sha256': request['media']['sha256'],
            'definition_sha256': io.sha256(out/'mai-definition.json'), 'automatic_retries': 0, 'at': io.now()})
    result = subprocess.run(command, input=config, text=True, encoding='utf-8', errors='replace', capture_output=True, check=False)
    io.save(out / 'http-result.json', {'curl_exit_code': result.returncode, 'http_status': result.stdout.strip(), 'at': io.now()})
    if result.returncode or result.stdout.strip() != '200':
        raise io.IntakeError('MAI request failed or outcome is unknown; retained response, no automatic resubmission.')
    # Confirm parse before accepting the raw result. No model/batch retry on schema errors.
    raw = io.load(partial)
    if not isinstance(raw, dict):
        raise io.IntakeError('MAI response is not a JSON object.')
    raw_path = out / 'mai.raw.json'
    partial.rename(raw_path)
    body.unlink()
    return raw_path, mai_canonical(raw)


def transcribe_local(request, out):
    spec = request['spec']; command = local_command(spec)
    work = out / 'provider'
    if spec['driver'] == 'runtime':
        command += ['transcribe', '--file', request['media']['path'], '--output-dir', str(work), '--offset-ms', '0', '--model', spec['model']]
        if request.get('language'):
            command += ['--language', request['language']]
        if request['offline']:
            command += ['--offline']
        raw_path = work / 'whisperx.result.json'
    else:
        work.mkdir()
        command += [request['media']['path'], '--model', spec['model'], '--task', 'transcribe',
                    '--output_format', 'json', '--output_dir', str(work), '--interpolate_method', 'ignore']
        if request.get('language'):
            command += ['--language', request['language']]
        if request['offline']:
            raise io.IntakeError('Strict offline mode requires the compatible runtime; CLI model_cache_only is not a full network guarantee.')
        raw_path = work / (Path(request['media']['path']).stem + '.json')
    io.save(out / 'local-command.json', {'command': command, 'model_requested': spec['model']})
    io.run(command, 'WhisperX', timeout=3600)
    raw = io.load(raw_path)
    if raw.get('input_sha256') and raw['input_sha256'] != request['media']['sha256']:
        raise io.IntakeError('WhisperX result belongs to a different audio input.')
    return raw_path, raw


def child_path(out, value):
    if not isinstance(value, str) or not value:
        raise io.IntakeError('ASR result path is missing.')
    p = Path(value); p = (p if p.is_absolute() else out/p).resolve()
    if not p.is_relative_to(out.resolve()) or not p.is_file():
        raise io.IntakeError('ASR artifacts must be files in this attempt directory.')
    return p


def transcribe_custom(request, out):
    definition = adapter(request['spec'])
    values = {'request_json': str(out/'request.json'), 'output_dir': str(out),
              'result_json': str(out/'adapter-result.json'), 'python': sys.executable}
    command = []
    for value in definition['command']:
        for key, replacement in values.items():
            value = value.replace('{'+key+'}', replacement)
        command.append(value)
    command[0] = io.binary(command[0])
    io.run(command, 'Custom ASR', timeout=definition.get('timeout_seconds', 3600))
    receipt = io.load(out/'adapter-result.json')
    if (receipt.get('schema_version') != 1 or receipt.get('status') != 'completed'
            or receipt.get('input_sha256') != request['media']['sha256']
            or receipt.get('time_basis') != 'input_relative_seconds'):
        raise io.IntakeError('Custom ASR receipt must bind this input and declare input_relative_seconds.')
    raw = child_path(out, receipt.get('raw_path'))
    canonical = child_path(out, receipt.get('transcript_path'))
    return raw, io.load(canonical)


def finalize(out, request, raw_path, canonical):
    if io.sha256(request['media']['path']) != request['media']['sha256']:
        raise io.IntakeError('Input audio changed during ASR.')
    # Freeze canonical raw seconds before normalization; recover can reuse this without API calls.
    io.save(out/'canonical.json', canonical)
    io.save(out/'provider-receipt.json', {'request_sha256': io.sha256(out/'request.json'),
            'raw_path': str(raw_path), 'raw_sha256': io.sha256(raw_path),
            'canonical_sha256': io.sha256(out/'canonical.json')})
    return materialize(out, request)


def materialize(out, request):
    receipt = io.load(out/'provider-receipt.json')
    raw = child_path(out, receipt['raw_path'])
    if (io.sha256(out/'request.json') != receipt['request_sha256'] or io.sha256(raw) != receipt['raw_sha256']
            or io.sha256(out/'canonical.json') != receipt['canonical_sha256']
            or io.sha256(request['media']['path']) != request['media']['sha256']):
        raise io.IntakeError('ASR recovery evidence changed; no reuse or resubmission.')
    document = normalize(io.load(out/'canonical.json'), request)
    target = out/('source.json' if document['segments'] else 'no-speech.json')
    if target.exists():
        if io.load(target) != document:
            raise io.IntakeError('Existing normalized output differs from verified replay.')
    else:
        io.save(target, document)
    result = {'schema_version': 1, 'status': 'completed' if document['segments'] else 'no_speech_review_required',
              'backend': request['backend'], 'segments': len(document['segments']), 'offset_ms': request['offset_ms'],
              'artifacts': {str(p.relative_to(out)): io.sha256(p) for p in
                            [out/'request.json', raw, out/'canonical.json', out/'provider-receipt.json', target]},
              'input_path': request['media']['path'], 'input_sha256': request['media']['sha256'], 'at': io.now()}
    io.save(out/'result.json', result)
    return {'status': result['status'], 'segments': result['segments'], 'result': str(out/'result.json')}


def transcribe(args):
    selected, spec = choose(args)
    if args.offset_ms < 0:
        raise io.IntakeError('offset-ms must be nonnegative.')
    source = Path(args.file).expanduser().resolve(strict=True)
    if source.suffix.lower() not in ('.wav', '.mp3', '.flac'):
        raise io.IntakeError('Use a WAV, MP3 or FLAC audio block prepared from the verified media timeline.')
    if not 0 < source.stat().st_size < 250_000_000:
        raise io.IntakeError('Use an audio block smaller than 250 MB.')
    info = io.probe(source, io.binary(args.ffprobe))
    io.validate_media(info, False, False)
    if info['duration_ms'] > 900_000:
        raise io.IntakeError('Use blocks of at most 15 minutes; prepare long media with audio_timeline.py.')
    if args.offline and (selected != 'whisperx' or spec['driver'] != 'runtime'):
        raise io.IntakeError('Strict offline mode is available only for the compatible local WhisperX runtime.')
    if selected == 'mai-transcribe-2':
        if not spec['endpoint'] or not os.environ.get(spec['key_env']):
            raise io.IntakeError('Configure an Azure region (default) or Foundry/Speech endpoint and its matching ' + spec['key_env'] + ' locally; no upload was started.')
        io.binary('curl')
    elif selected == 'whisperx':
        local_command(spec)
    else:
        missing = [x for x in spec.get('required_env', []) if not os.environ.get(x)]
        if missing:
            raise io.IntakeError('Missing custom ASR environment variables: ' + ', '.join(missing))
    out = Path(args.output_dir).expanduser().resolve(); out.mkdir(parents=True, exist_ok=False)
    request = {'schema_version': 1, 'backend': selected, 'spec': spec, 'offset_ms': args.offset_ms,
               'language': args.language, 'offline': args.offline, 'at': io.now(),
               'media': {'path': str(source), 'sha256': io.sha256(source), **info}}
    if selected == 'whisperx':
        request['tool_side_effects'] = runtime_side_effects(spec)
    io.save(out/'request.json', request)
    try:
        raw, canonical = (transcribe_local(request, out) if selected == 'whisperx' else
                          transcribe_mai(request, out) if selected == 'mai-transcribe-2' else transcribe_custom(request, out))
        return finalize(out, request, raw, canonical)
    except Exception as error:
        message = str(error) if isinstance(error, io.IntakeError) else type(error).__name__ + '; inspect retained evidence locally.'
        io.save(out/'failure.json', {'status': 'failed_or_uncertain', 'message': message, 'automatic_retries': 0})
        raise io.IntakeError(message) from None


def verify(args):
    out = Path(args.output_dir).resolve(); result = io.load(out/'result.json')
    if result.get('schema_version') != 1 or result.get('status') not in ('completed', 'no_speech_review_required'):
        raise io.IntakeError('ASR result is not complete.')
    expected_source = 'source.json' if result['status'] == 'completed' else 'no-speech.json'
    required = {'request.json', 'canonical.json', 'provider-receipt.json', expected_source}
    if not isinstance(result.get('artifacts'), dict) or not required.issubset(result['artifacts']):
        raise io.IntakeError('ASR result is missing required evidence.')
    receipt = io.load(out/'provider-receipt.json')
    raw = child_path(out, receipt['raw_path'])
    if str(raw.relative_to(out)) not in result['artifacts']:
        raise io.IntakeError('ASR result is missing raw evidence.')
    for path, expected in result['artifacts'].items():
        if io.sha256(child_path(out, path)) != expected:
            raise io.IntakeError('ASR artifact changed.')
    if io.sha256(result['input_path']) != result['input_sha256']:
        raise io.IntakeError('ASR input changed.')
    return {'status': 'verified', 'asr_status': result['status'], 'segments': result['segments']}


def recover(args):
    out = Path(args.output_dir).resolve()
    if (out/'result.json').exists():
        return verify(args)
    if not (out/'provider-receipt.json').exists():
        raise io.IntakeError('No frozen provider receipt; inspect old process/raw response. Recovery never calls an ASR backend.')
    return materialize(out, io.load(out/'request.json'))


def parser():
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='action', required=True)
    c = sub.add_parser('configure'); c.set_defaults(func=configure)
    c.add_argument('--config'); c.add_argument('--backend', type=name); c.add_argument('--adapter')
    address = c.add_mutually_exclusive_group()
    address.add_argument('--endpoint'); address.add_argument('--region', type=speech_region)
    c.add_argument('--key-env', type=key_env_name); c.add_argument('--local-driver', choices=('cli', 'runtime'))
    c.add_argument('--runtime-root'); c.add_argument('--whisperx-executable')
    c = sub.add_parser('check'); c.set_defaults(func=check)
    c.add_argument('--config'); c.add_argument('--backend', type=name); c.add_argument('--ffprobe', default='ffprobe')
    c = sub.add_parser('transcribe'); c.set_defaults(func=transcribe)
    c.add_argument('--config'); c.add_argument('--backend', type=name); c.add_argument('--file', required=True)
    c.add_argument('--output-dir', required=True); c.add_argument('--offset-ms', type=int, default=0)
    c.add_argument('--language'); c.add_argument('--offline', action='store_true'); c.add_argument('--ffprobe', default='ffprobe')
    for command, function in [('verify', verify), ('recover', recover)]:
        c = sub.add_parser(command); c.set_defaults(func=function); c.add_argument('--output-dir', required=True)
    return p


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        print(json.dumps(args.func(args), ensure_ascii=False, indent=2)); return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        message = str(error) if isinstance(error, io.IntakeError) else type(error).__name__ + '; check local configuration/artifacts.'
        print(json.dumps({'status': 'error', 'message': message}, ensure_ascii=False), file=sys.stderr); return 2


if __name__ == '__main__':
    raise SystemExit(main())
