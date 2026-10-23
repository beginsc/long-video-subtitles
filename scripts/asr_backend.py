"""Optional online ASR. Upload/submit once, bounded status polling, normalized artifacts."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from asr_credentials import read_key
from subtitle_workbench import row, structural, save

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / 'assets' / 'asr-backends.json'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def checkpoint(path, data):
    """Only updates this command's job manifest, not user transcript artifacts."""
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     suffix='.tmp', delete=False) as stream:
        tmp = Path(stream.name)
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ApiError(ValueError):
    pass


class AssemblyAI:
    HOSTS = {'https://api.assemblyai.com', 'https://api.eu.assemblyai.com'}
    OPTIONS = {'speech_models', 'language_code', 'language_detection', 'speaker_labels',
               'punctuate', 'format_text', 'keyterms_prompt'}

    def __init__(self, config):
        self.config = config
        self.base = config['base_url'].rstrip('/')
        if self.base not in self.HOSTS:
            raise ValueError('AssemblyAI credentials may only be sent to its official US/EU API hosts')
        self.key = read_key(config)
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method, route, data=None, media=None, timeout=None):
        if not route.startswith('/v2/') or '://' in route:
            raise ValueError('Invalid API route')
        headers = {'Authorization': self.key, 'User-Agent': 'long-video-subtitles/1'}
        body = None
        if media:
            body = media
            headers['Content-Type'] = 'application/octet-stream'
            headers['Content-Length'] = str(os.fstat(media.fileno()).st_size)
        elif data is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(data).encode('utf-8')
        request = urllib.request.Request(self.base + route, data=body, headers=headers, method=method)
        # No automatic retry for POST: a timeout can hide an accepted paid job.
        try:
            with self.opener.open(request, timeout=timeout or self.config.get('request_timeout_seconds', 30)) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            labels = {401: 'authentication rejected', 403: 'access denied',
                      429: 'rate limited', 402: 'account billing required'}
            raise ApiError(f'AssemblyAI HTTP {code}: {labels.get(code, "request failed")}; response body not logged') from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ApiError('AssemblyAI connection failed or timed out; POST acceptance may be unknown') from None

    def check_auth(self):
        self.request('GET', '/v2/transcript?limit=1')
        return {'backend': 'assemblyai', 'authentication': 'ok', 'submitted_jobs': 0}

    def upload(self, path):
        with Path(path).open('rb') as stream:
            return self.request('POST', '/v2/upload', media=stream,
                                timeout=self.config.get('upload_timeout_seconds', 300))['upload_url']

    def submit(self, audio_url):
        payload = {k: copy.deepcopy(v) for k, v in self.config.items() if k in self.OPTIONS}
        payload['audio_url'] = audio_url
        return self.request('POST', '/v2/transcript', data=payload)

    def transcript(self, transcript_id):
        return self.request('GET', '/v2/transcript/' + valid_id(transcript_id))

    def sentences(self, transcript_id):
        return self.request('GET', '/v2/transcript/' + valid_id(transcript_id) + '/sentences')


ADAPTERS = {'assemblyai': AssemblyAI}


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise ValueError('Invalid transcript ID')
    return value


def config_for(args):
    registry = load(args.config)
    if registry.get('schema_version') != 1:
        raise ValueError('Unsupported backend configuration version')
    selected = args.backend or registry.get('default_backend')
    if not selected:
        raise ValueError('Online ASR is optional: select --backend assemblyai explicitly')
    config = copy.deepcopy(registry['backends'][selected])
    if config.get('adapter') not in ADAPTERS:
        raise ValueError('Provider adapter is not implemented')
    if getattr(args, 'model', None):
        config['speech_models'] = args.model
    if getattr(args, 'language', None):
        config['language_code'] = args.language
        config['language_detection'] = False
    models = config.get('speech_models')
    if not isinstance(models, list) or not models or any(not isinstance(m, str) or not m.strip() for m in models):
        raise ValueError('speech_models must be a nonempty list of model IDs')
    if config.get('language_code') and config.get('language_detection'):
        raise ValueError('Use language_code or language_detection, not both')
    forbidden = set(config) - (AssemblyAI.OPTIONS | {'adapter', 'base_url', 'api_key_env',
                'credential_name', 'request_timeout_seconds', 'upload_timeout_seconds'})
    if forbidden:
        raise ValueError('Unsupported configuration fields; use environment/DPAPI for credentials')
    return selected, config


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def submit_job(args):
    name, config = config_for(args)
    client = ADAPTERS[config['adapter']](config)
    if Path(args.job).exists():
        raise ValueError('Job manifest exists: use status to resume; do not submit again')
    if args.file:
        media = Path(args.file).resolve(strict=True)
        if not media.is_file() or not 0 < media.stat().st_size <= 2_000_000_000:
            raise ValueError('Local upload must be nonempty and <= 2 GB; extract/compress or chunk larger media')
        source = {'file_name': media.name, 'sha256': file_hash(media)}
    else:
        url = urllib.parse.urlsplit(args.audio_url)
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            raise ValueError('Use an HTTPS media URL without embedded credentials')
        source = {'url_sha256': hashlib.sha256(args.audio_url.encode()).hexdigest()}
    job = {'schema_version': 1, 'backend': name, 'config': config, 'source': source,
           'phase': 'prepared', 'offset_ms': args.offset_ms}
    save(args.job, job)
    try:
        if args.file:
            job['phase'] = 'uploading'
            checkpoint(args.job, job)
            audio_url = client.upload(media)
        else:
            audio_url = args.audio_url
        # URL stays in the local job manifest for recovery, never in console output.
        job.update(audio_url=audio_url, phase='submitting')
        checkpoint(args.job, job)
        response = client.submit(audio_url)
        job.update(transcript_id=valid_id(response['id']), phase=response['status'])
        checkpoint(args.job, job)
    except (ValueError, KeyError, OSError):
        job['phase'] = 'submission_uncertain' if job['phase'] == 'submitting' else 'upload_failed'
        checkpoint(args.job, job)
        raise
    print(json.dumps({'backend': name, 'phase': job['phase'], 'transcript_id': job['transcript_id']}))


def normalize(transcript, sentences, job):
    if transcript.get('status') != 'completed':
        raise ValueError('Transcript is not completed')
    offset = job.get('offset_ms', 0)
    if type(offset) is not int or offset < 0:
        raise ValueError('offset_ms must be a nonnegative integer')
    rows = []
    for sentence in sentences['sentences']:
        start, end = sentence['start'], sentence['end']
        if type(start) is not int or type(end) is not int:
            raise ValueError('AssemblyAI sentence timestamps must be integer milliseconds')
        r = row(f's{len(rows)+1:06d}', start + offset, end + offset, sentence['text'])
        r['asr_confidence'] = sentence.get('confidence')
        r['asr_words'] = []
        for word in sentence.get('words', []):
            if type(word['start']) is not int or type(word['end']) is not int:
                raise ValueError('AssemblyAI word timestamps must be integer milliseconds')
            r['asr_words'].append({'word': word['text'], 'start': word['start']/1000,
                'end': word['end']/1000, 'confidence': word.get('confidence'), 'speaker': word.get('speaker')})
        r['asr_words_offset_ms'] = offset
        speakers = {w['speaker'] for w in r['asr_words'] if w['speaker'] is not None}
        if len(speakers) == 1:
            r['speaker'] = next(iter(speakers))
        elif len(speakers) > 1:
            r['issues'].append('Multiple ASR speaker labels in sentence; inspect segmentation')
        rows.append(r)
    # Provider audio_duration is retained as evidence, not treated as ffprobe's full media duration.
    doc = {'schema_version': 1, 'meta': {'asr_backend': job['backend'],
        'asr_transcript_id': transcript['id'], 'asr_model_used': transcript.get('speech_model_used'),
        'source_language': transcript.get('language_code'),
        'asr_audio_duration_seconds': transcript.get('audio_duration'),
        'input_sha256': job['source'].get('sha256'), 'offset_ms': offset}, 'segments': rows}
    structural(doc)
    return doc


def status_job(args):
    job = load(args.job)
    client = ADAPTERS[job['config']['adapter']](job['config'])
    transcript_id = job.get('transcript_id')
    if not transcript_id:
        raise ValueError('No transcript ID saved; check provider dashboard before any resubmission; use attach to recover ID')
    deadline = time.monotonic() + args.wait_seconds
    while True:
        response = client.transcript(transcript_id)
        job['phase'] = response['status']
        checkpoint(args.job, job)
        if response['status'] in ('completed', 'error') or time.monotonic() >= deadline:
            break
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    print(json.dumps({'backend': job['backend'], 'phase': job['phase'], 'transcript_id': transcript_id}))
    if job['phase'] == 'error':
        raise ValueError('Remote transcription failed; inspect this transcript in AssemblyAI dashboard')
    if job['phase'] == 'completed' and args.output_dir:
        out = Path(args.output_dir)
        if out.exists():
            raise ValueError('Output directory exists; use another version to avoid overwriting')
        sentences = client.sentences(transcript_id)
        # Keep original provider evidence, excluding signed audio URL and webhook auth fields.
        raw = {k: v for k, v in response.items() if k not in
               {'audio_url', 'webhook_url', 'webhook_auth_header_name', 'webhook_auth_header_value'}}
        out.mkdir(parents=True, exist_ok=False)
        save(out / 'assemblyai.transcript.json', raw)
        save(out / 'assemblyai.sentences.json', sentences)
        doc = normalize(response, sentences, job)
        save(out / 'source.json', doc)
        print(f'Exported {len(doc["segments"])} ASR sentences; translation/review still pending')


def attach_job(args):
    job = load(args.job)
    if job.get('transcript_id'):
        raise ValueError('Job already has a transcript ID')
    client = ADAPTERS[job['config']['adapter']](job['config'])
    response = client.transcript(args.transcript_id)
    if response.get('audio_url') != job.get('audio_url') or not job.get('audio_url'):
        raise ValueError('Recovered transcript media URL does not match the submitted job')
    job.update(transcript_id=valid_id(response['id']), phase=response['status'])
    checkpoint(args.job, job)
    print('Existing remote job attached without submitting a new job')


def main():
    from whisperx_backend import maybe_dispatch
    routed = maybe_dispatch(sys.argv[1:])
    if routed is not None:
        return routed
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('check', 'submit'):
        p = sub.add_parser(name)
        p.add_argument('--config', default=str(DEFAULT_CONFIG))
        p.add_argument('--backend')
        if name == 'submit':
            group = p.add_mutually_exclusive_group(required=True)
            group.add_argument('--file')
            group.add_argument('--audio-url')
            p.add_argument('--job', required=True)
            p.add_argument('--model', action='append')
            p.add_argument('--language')
            p.add_argument('--offset-ms', type=int, default=0)
    p = sub.add_parser('status')
    p.add_argument('--job', required=True)
    p.add_argument('--wait-seconds', type=int, default=0, choices=range(0, 46), metavar='0..45')
    p.add_argument('--output-dir')
    p = sub.add_parser('attach')
    p.add_argument('--job', required=True)
    p.add_argument('--transcript-id', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'check':
            _, config = config_for(args)
            print(json.dumps(ADAPTERS[config['adapter']](config).check_auth()))
        elif args.command == 'submit':
            if args.offset_ms < 0:
                raise ValueError('offset-ms must be nonnegative')
            submit_job(args)
        elif args.command == 'status':
            status_job(args)
        else:
            attach_job(args)
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        # Never print request headers, server bodies, full config, or signed URLs.
        message = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else 'Invalid data, file access, or API response'
        print('ERROR: ' + message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
