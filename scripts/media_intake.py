"""Acquire one media item; user preferences live outside the installed skill.

Python 3.9+, standard library. ffprobe validates every media input. URL input
uses either yt-dlp or SnapAny's documented public API, never an implicit fallback.
"""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener, urlopen

SNAPANY_ENDPOINT = 'https://api.snapany.com/openapi/v1/extract/post'
KEY_ENV = 'SNAPANY_API_KEY'


class IntakeError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, data, replace=False):
    """Use an owned directory. Only mutable user preferences use replace=True."""
    path = Path(path)
    text = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    if not replace:
        with path.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.download-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def config_path(value=None):
    if value or os.environ.get('LVS_DOWNLOAD_CONFIG'):
        return Path(value or os.environ['LVS_DOWNLOAD_CONFIG']).expanduser().resolve()
    base = (Path(os.environ.get('APPDATA', Path.home() / 'AppData' / 'Roaming'))
            if os.name == 'nt' else Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')))
    return base / 'long-video-subtitles' / 'download.json'


def positive(value):
    number = int(value)
    if number <= 0:
        raise IntakeError('max-height must be positive.')
    return number


def read_config(path):
    if not path.exists():
        return None
    value = load(path)
    if (not isinstance(value, dict) or value.get('schema_version') != 1
            or value.get('backend') not in ('local', 'remote')):
        raise IntakeError('Invalid download config; run configure to choose local or remote.')
    positive(value.get('max_height', 1080))
    return value


def configure(args):
    backend = args.backend
    if not backend:
        if not sys.stdin.isatty():
            raise IntakeError('Choose --backend local (yt-dlp) or remote (SnapAny). No default selected.')
        print('视频链接下载方式 / Video URL downloader:')
        print('1. 本地 local: yt-dlp 在本机运行；需安装 yt-dlp、FFmpeg。')
        print('2. 远程 remote: 视频链接发送到 SnapAny 解析；需 API key，可能计费；视频仍下载到本机。')
        answer = input('选择 / Choose [1/2]: ').strip().lower()
        backend = {'1': 'local', 'local': 'local', '2': 'remote', 'remote': 'remote'}.get(answer)
        if not backend:
            raise IntakeError('No selection saved. Run configure again.')
    path = config_path(args.config)
    # Preserve preferences when changing backend unless a new height was supplied.
    try:
        old = read_config(path) if path.exists() else {}
    except (ValueError, TypeError):
        # An explicit new choice also repairs a malformed old preference file.
        old = {}
    value = {'schema_version': 1, 'backend': backend,
             'max_height': positive(args.max_height or old.get('max_height', 1080)),
             'configured_at': now()}
    save(path, value, replace=True)
    return {'status': 'configured', 'config_path': str(path), **value,
            'next': 'Run check. This command does not install dependencies or contact providers.'}


def classify(value):
    # Check files first: Windows drive letters are not URL schemes.
    path = Path(value).expanduser()
    if path.is_file():
        return 'file', str(path.resolve())
    parsed = urlsplit(value)
    if parsed.scheme.lower() in ('http', 'https'):
        validate_url(value)
        return 'url', value
    raise IntakeError('Input is not an existing local file or an HTTP(S) video URL.')


def validate_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or any(c in value for c in ('\r', '\n', '\x00'))):
        raise IntakeError('Expected an HTTP(S) URL without embedded credentials.')
    return value


def binary(name):
    result = shutil.which(name)
    if not result:
        raise IntakeError(f'Missing executable: {name}. Install it or supply its path.')
    return result


def run(command, label, timeout=None):
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise IntakeError(f'{label} timed out; keep this attempt and inspect its files.') from None
    if result.returncode:
        # Tool errors can contain signed URLs/cookies. Never echo raw stderr.
        error = result.stderr.lower()
        hint = 'check dependencies, access and input'
        for tokens, explanation in [
            (('sign in', 'login', 'cookies'), 'site requires login; provide an authorized cookie file for local mode'),
            (('javascript', 'js runtime', 'challenge'), 'check yt-dlp version and its JavaScript runtime'),
            (('requested format', 'no video formats'), 'no matching format; inspect the quality limit and site access'),
            (('unsupported url',), 'this URL is not supported by the selected downloader'),
            (('403', 'forbidden'), 'download access was denied (HTTP 403)'),
            (('certificate', 'ssl'), 'check TLS certificates; certificate verification was not disabled'),
        ]:
            if any(token in error for token in tokens):
                hint = explanation
                break
        raise IntakeError(f'{label} failed (exit {result.returncode}); {hint}.')
    return result.stdout


def ytdlp_command(executable=None):
    if executable:
        return [binary(executable)]
    if shutil.which('yt-dlp'):
        return [shutil.which('yt-dlp')]
    if importlib.util.find_spec('yt_dlp'):
        return [sys.executable, '-m', 'yt_dlp']
    raise IntakeError('yt-dlp is missing. See references/media-intake.md for installation.')


def probe(path, ffprobe):
    raw = json.loads(run([ffprobe, '-v', 'error', '-show_format', '-show_streams',
                          '-of', 'json', str(path)], 'ffprobe', timeout=60))
    streams = []
    for item in raw.get('streams', []):
        streams.append({k: item[k] for k in ('index', 'codec_type', 'codec_name', 'width', 'height',
                       'sample_rate', 'channels', 'start_time', 'duration') if k in item})
        streams[-1]['language'] = item.get('tags', {}).get('language')
        streams[-1]['attached_pic'] = bool(item.get('disposition', {}).get('attached_pic'))
    duration = float(raw.get('format', {}).get('duration', 0))
    if not math.isfinite(duration) or duration <= 0:
        raise IntakeError('Media has no finite positive duration; select a completed video.')
    return {'duration_ms': round(duration * 1000), 'streams': streams,
            'format_name': raw.get('format', {}).get('format_name'),
            'start_time': raw.get('format', {}).get('start_time')}


def validate_media(info, require_video, allow_silent):
    video = any(s.get('codec_type') == 'video' and not s.get('attached_pic') for s in info['streams'])
    audio = any(s.get('codec_type') == 'audio' for s in info['streams'])
    if require_video and not video:
        raise IntakeError('Downloaded item has no video stream; no ready manifest was written.')
    if not audio and not (video and allow_silent):
        raise IntakeError('No audio stream found; select the audio track or explicitly allow silent video.')


def yt_download(url, out, height, command, ffmpeg, cookies=None, js_runtime=None):
    base = command + ['--ignore-config', '--no-plugin-dirs', '--no-playlist', '--no-mark-watched',
                      '--no-write-subs', '--no-write-auto-subs', '--no-cache-dir', '--no-progress',
                      '--socket-timeout', '30', '--retries', '2', '--fragment-retries', '2',
                      '--ffmpeg-location', str(Path(ffmpeg).parent)]
    if cookies:
        # yt-dlp can update the jar. Never give it the user's original file.
        private = out / 'cookies.private.txt'
        shutil.copyfile(Path(cookies).expanduser(), private)
        base += ['--cookies', str(private)]
    if js_runtime:
        base += ['--js-runtimes', js_runtime]
    # Inspect before downloading: --no-playlist alone doesn't reject a playlist URL.
    metadata = json.loads(run(base + ['--flat-playlist', '--playlist-end', '1', '--dump-single-json',
                                     '--skip-download', '--', url], 'yt-dlp metadata', timeout=180))
    if metadata.get('_type') in ('playlist', 'multi_video') or 'entries' in metadata:
        raise IntakeError('URL resolves to multiple videos; supply one episode/part URL.')
    if metadata.get('is_live') or metadata.get('live_status') in ('is_live', 'is_upcoming'):
        raise IntakeError('Live/upcoming video requires a bounded recording task, not this downloader.')
    receipt = out / 'yt-dlp-files.private.jsonl'
    selector = f'bv*[height<={height}]+ba/b[height<={height}]'
    template = str(out).replace('%', '%%') + os.sep + 'media.%(ext)s'
    run(base + ['--no-overwrites', '--continue', '--format', selector,
                '--merge-output-format', 'mkv', '--output', template,
                '--print-to-file', 'after_move:%(filepath)j', str(receipt), '--', url], 'yt-dlp download')
    paths = [Path(json.loads(line)).resolve() for line in receipt.read_text(encoding='utf-8').splitlines() if line.strip()]
    if len(paths) != 1 or paths[0].parent != out.resolve() or not paths[0].is_file():
        raise IntakeError('yt-dlp did not return exactly one local media file in this attempt.')
    return paths[0], {'provider': 'yt-dlp', 'version': run(command + ['--version'], 'yt-dlp version', 30).strip(),
                      'video_id': metadata.get('id'), 'title': metadata.get('title'),
                      'format_selector': selector, 'subtitles_downloaded': False}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def extract_snapany(url, out, cached=None):
    url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()
    if cached:
        wrapper = load(cached)
        if (wrapper.get('schema_version') != 1 or wrapper.get('source_url_sha256') != url_hash
                or wrapper.get('endpoint') != SNAPANY_ENDPOINT or not isinstance(wrapper.get('response'), dict)):
            raise IntakeError('Cached SnapAny response does not match this source URL/API.')
        save(out / 'snapany-response.private.json', wrapper)
        return wrapper['response'], 'cached'
    key = os.environ.get(KEY_ENV, '').strip()
    if not key:
        raise IntakeError(f'Set {KEY_ENV} in the local environment; never put the key in chat or config.')
    # Intent survives a network timeout. This function never retries a billable POST.
    save(out / 'snapany-request.json', {'schema_version': 1, 'endpoint': SNAPANY_ENDPOINT,
         'source_url_sha256': url_hash, 'submitted_at': now(), 'automatic_retries': 0})
    request = Request(SNAPANY_ENDPOINT, data=json.dumps({'url': url}).encode('utf-8'),
                      headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}, method='POST')
    try:
        with build_opener(NoRedirect()).open(request, timeout=90) as response:
            body = response.read(8 * 1024 * 1024 + 1)
        if len(body) > 8 * 1024 * 1024:
            raise IntakeError('SnapAny response exceeded 8 MiB; request may have been billed. Do not auto-resubmit.')
        data = json.loads(body)
    except HTTPError as exc:
        raise IntakeError(f'SnapAny HTTP {exc.code}; no automatic retry or provider switch.') from None
    except (URLError, TimeoutError, json.JSONDecodeError):
        raise IntakeError('SnapAny request failed or outcome is unknown; inspect request record before any resubmission.') from None
    if not isinstance(data, dict):
        raise IntakeError('Unexpected SnapAny response; do not automatically resubmit.')
    save(out / 'snapany-response.private.json', {'schema_version': 1, 'endpoint': SNAPANY_ENDPOINT,
         'source_url_sha256': url_hash, 'received_at': now(), 'response': data})
    return data, 'api'


def media_headers(*sources):
    result = {}
    for source in sources:
        for name, value in (source or {}).items():
            if (not isinstance(value, str) or not re.fullmatch(r'[!#$%&\x27*+.^_`|~0-9A-Za-z-]+', name)
                    or any(c in value for c in ('\r', '\n', '\x00'))):
                raise IntakeError('Invalid media request header.')
            # HTTP header names are case insensitive. Empty values are intentional.
            result[name.lower()] = value
    return result


def select_snapany(data, height, media_index=None):
    videos = [m for m in data.get('medias', []) if m.get('media_type') == 'video']
    if not videos:
        raise IntakeError('SnapAny returned no video. Response retained for inspection.')
    if media_index is None and len(videos) != 1:
        raise IntakeError(f'SnapAny returned {len(videos)} videos; choose --media-index (1-based) using the cached response.')
    index = 1 if media_index is None else media_index
    if index < 1 or index > len(videos):
        raise IntakeError('media-index is outside the returned video list.')
    media = videos[index - 1]
    variants = []
    for variant in media.get('variants', []):
        quality = variant.get('quality')
        if (variant.get('video_url') and isinstance(quality, (int, float))
                and 0 < quality <= height and quality != 9999):
            variants.append(variant)
    if variants:
        variant = max(variants, key=lambda v: (v['quality'], v.get('video_ext') == 'mp4'))
        return {'video_url': validate_url(variant['video_url']),
                'audio_url': validate_url(variant['audio_url']) if variant.get('audio_url') else None,
                'headers': media_headers(media.get('headers'), variant.get('headers')),
                'quality': variant['quality'], 'media_index': index}
    if media.get('variants'):
        raise IntakeError('No video variant at/below max-height; choose a larger limit after inspecting the cached response.')
    if not media.get('resource_url'):
        raise IntakeError('SnapAny video has no downloadable URL.')
    return {'video_url': validate_url(media['resource_url']), 'audio_url': None,
            'headers': media_headers(media.get('headers')), 'quality': None, 'media_index': index}


def download_http(url, destination, headers):
    """Stream a direct file. Failed/partial data is never treated as complete."""
    partial = destination.with_name(destination.name + '.part')
    try:
        with urlopen(Request(validate_url(url), headers=headers), timeout=60) as response:
            if response.status != 200:
                raise IntakeError('Expected a complete HTTP 200 media response.')
            kind = response.headers.get('Content-Type', '').lower()
            if any(x in kind for x in ('text/html', 'application/json', 'mpegurl', 'dash+xml')):
                raise IntakeError('Response is not a direct media file (HTML/JSON/HLS/DASH). Keep evidence and choose another variant.')
            expected = response.headers.get('Content-Length')
            count = 0
            with partial.open('xb') as stream:
                for block in iter(lambda: response.read(1024 * 1024), b''):
                    count += len(block)
                    stream.write(block)
            if not count or (expected is not None and count != int(expected)):
                raise IntakeError('Incomplete media transfer; .part retained, no ready manifest.')
        if destination.exists():
            raise IntakeError('Media destination already exists.')
        partial.rename(destination)
    except HTTPError as exc:
        raise IntakeError(f'Media download HTTP {exc.code}; reuse cached response or choose a new attempt. No API retry.') from None
    except (URLError, TimeoutError):
        raise IntakeError('Media download failed; partial file retained. No API retry.') from None


def snap_download(url, out, height, ffmpeg, ffprobe, media_index=None, cached=None):
    data, origin = extract_snapany(url, out, cached)
    choice = select_snapany(data, height, media_index)
    video = out / 'video.download'
    download_http(choice['video_url'], video, choice['headers'])
    if choice['audio_url']:
        audio = out / 'audio.download'
        download_http(choice['audio_url'], audio, choice['headers'])
        path = out / 'media.mkv'
        run([ffmpeg, '-v', 'error', '-nostdin', '-n', '-i', str(video), '-i', str(audio),
             '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy', str(path)], 'FFmpeg stream merge')
    else:
        # Probe content, not the returned URL extension; remux without re-encoding.
        info = probe(video, ffprobe)
        validate_media(info, require_video=True, allow_silent=True)
        path = out / 'media.mkv'
        run([ffmpeg, '-v', 'error', '-nostdin', '-n', '-i', str(video), '-map', '0:v:0',
             '-map', '0:a?', '-c', 'copy', str(path)], 'FFmpeg remux')
    return path, {'provider': 'snapany', 'endpoint': SNAPANY_ENDPOINT, 'response_origin': origin,
                  'title': data.get('title'), 'video_id': data.get('id'),
                  'media_index': choice['media_index'], 'selected_height': choice['quality'],
                  'subtitles_downloaded': False, 'automatic_api_retries': 0}


def acquire(args):
    kind, source = classify(args.input)
    # Local files bypass downloader configuration, API keys and yt-dlp entirely.
    config = (read_config(config_path(args.config)) or {}) if kind == 'url' else {}
    backend = (args.backend or config.get('backend')) if kind == 'url' else 'file'
    if kind == 'url' and not backend:
        raise IntakeError('Download preference missing. Run configure and choose local or remote before URL processing.')
    height = positive(args.max_height or config.get('max_height', 1080))
    if kind == 'file' and (args.media_index or args.snapany_response or args.cookies_file or args.js_runtime):
        raise IntakeError('Downloader options do not apply to local files.')
    if backend != 'remote' and (args.snapany_response or args.media_index):
        raise IntakeError('SnapAny options require --backend remote.')
    if backend == 'remote' and (args.cookies_file or args.js_runtime or args.yt_dlp):
        raise IntakeError('yt-dlp options require --backend local; cookies are not sent to SnapAny.')
    fp = binary(args.ffprobe)
    ff = binary(args.ffmpeg) if kind == 'url' else None
    command = ytdlp_command(args.yt_dlp) if backend == 'local' else None
    if backend == 'remote' and not args.snapany_response and not os.environ.get(KEY_ENV, '').strip():
        raise IntakeError(f'Missing {KEY_ENV}. Configure the environment locally before downloading.')
    out = Path(args.output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=False)
    save(out / 'request.json', {'schema_version': 1, 'started_at': now(), 'input_kind': kind,
         'source': source, 'backend': backend, 'max_height': height, 'allow_silent': args.allow_silent})
    try:
        if backend == 'file':
            media, acquisition = Path(source), {'provider': 'file', 'original_read_only': True}
        elif backend == 'local':
            media, acquisition = yt_download(source, out, height, command, ff, args.cookies_file, args.js_runtime)
        else:
            media, acquisition = snap_download(source, out, height, ff, fp, args.media_index, args.snapany_response)
        info = probe(media, fp)
        validate_media(info, require_video=(kind == 'url'), allow_silent=args.allow_silent)
        warnings = []
        if any(s.get('height', 0) > height for s in info['streams']) and kind == 'url':
            warnings.append('Actual height exceeds preference; provider supplied an unlabelled direct file. No resize applied.')
        result = {'schema_version': 1, 'status': 'ready', 'completed_at': now(),
                  'input_kind': kind, 'source': source, 'acquisition': acquisition,
                  'media': {'path': str(media.resolve()), 'sha256': sha256(media), 'bytes': media.stat().st_size, **info},
                  'warnings': warnings, 'analysis_status': 'not_started'}
        save(out / 'media-manifest.json', result)
        return {'status': 'ready', 'manifest': str(out / 'media-manifest.json'), 'media_path': str(media), 'warnings': warnings}
    except Exception as exc:
        message = str(exc) if isinstance(exc, IntakeError) else f'{type(exc).__name__}; inspect this attempt locally.'
        save(out / 'failure.json', {'status': 'failed', 'at': now(), 'reason': message,
             'next': 'Preserve this directory. Use a new attempt; reuse cached SnapAny response if present. Do not auto-resubmit paid API requests.'})
        raise IntakeError(message) from None


def verify(args):
    manifest = load(args.manifest)
    if manifest.get('status') != 'ready' or manifest.get('schema_version') != 1:
        raise IntakeError('Not a ready media manifest.')
    media = manifest['media']
    path = Path(media['path'])
    if not path.is_file() or path.stat().st_size != media['bytes'] or sha256(path) != media['sha256']:
        raise IntakeError('Media missing or changed; acquisition must be revalidated.')
    return {'status': 'verified', 'manifest': str(Path(args.manifest).resolve()), 'media_path': str(path)}


def check(args):
    path = config_path(args.config)
    config = read_config(path)
    result = {'config_path': str(path), 'configured': config is not None, 'config': config,
              'ffmpeg': shutil.which(args.ffmpeg), 'ffprobe': shutil.which(args.ffprobe),
              'snapany_key_present': bool(os.environ.get(KEY_ENV, '').strip()), 'network_tested': False}
    try:
        cmd = ytdlp_command(args.yt_dlp)
        result['yt_dlp_version'] = run(cmd + ['--version'], 'yt-dlp version', 30).strip()
    except IntakeError as exc:
        result['yt_dlp_error'] = str(exc)
    return result


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    c = sub.add_parser('configure', help='Choose and save the download backend (no network).')
    c.add_argument('--backend', choices=('local', 'remote'))
    c.add_argument('--config')
    c.add_argument('--max-height', type=positive)
    c.set_defaults(func=configure)
    c = sub.add_parser('check', help='Read preferences and check installed tools (no network).')
    c.add_argument('--config')
    for name in ('ffmpeg', 'ffprobe'):
        c.add_argument('--' + name, default=name)
    c.add_argument('--yt-dlp')
    c.set_defaults(func=check)
    c = sub.add_parser('acquire', help='Register a local file or download one video URL.')
    c.add_argument('--input', required=True)
    c.add_argument('--output-dir', required=True)
    c.add_argument('--config')
    c.add_argument('--backend', choices=('local', 'remote'), help='One-task override; does not change saved preference.')
    c.add_argument('--max-height', type=positive)
    c.add_argument('--media-index', type=int)
    c.add_argument('--snapany-response', help='Reuse a source-bound response.private.json in a NEW attempt.')
    c.add_argument('--cookies-file', help='Explicit local yt-dlp Netscape cookie jar; never read browser cookies automatically.')
    c.add_argument('--js-runtime', help='yt-dlp JS runtime, e.g. node or deno:/path/to/deno.')
    c.add_argument('--allow-silent', action='store_true')
    c.add_argument('--yt-dlp', help='Optional yt-dlp executable path.')
    for name in ('ffmpeg', 'ffprobe'):
        c.add_argument('--' + name, default=name)
    c.set_defaults(func=acquire)
    c = sub.add_parser('verify', help='Recheck the saved media size and SHA-256 before reuse.')
    c.add_argument('--manifest', required=True)
    c.set_defaults(func=verify)
    return p


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        print(json.dumps(args.func(args), ensure_ascii=False, indent=2))
        return 0
    except (IntakeError, OSError, ValueError) as exc:
        message = str(exc) if isinstance(exc, IntakeError) else f'{type(exc).__name__}; check local paths/configuration.'
        print(json.dumps({'status': 'error', 'message': message}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
