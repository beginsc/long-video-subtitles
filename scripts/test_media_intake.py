import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock
from urllib.error import HTTPError

import media_intake as m


def args(*values):
    return m.parser().parse_args(list(values))


class Response(io.BytesIO):
    def __init__(self, body, headers=None, status=200):
        super().__init__(body)
        self.headers = headers or {'Content-Length': str(len(body))}
        self.status = status


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_config_is_persistent_and_secret_free(self):
        config = self.root / 'user' / 'download.json'
        with patch.dict(os.environ, {m.KEY_ENV: 'sentinel-secret'}):
            m.configure(args('configure', '--backend', 'remote', '--max-height', '720', '--config', str(config)))
        m.configure(args('configure', '--backend', 'local', '--config', str(config)))
        self.assertEqual(m.read_config(config)['backend'], 'local')
        self.assertEqual(m.read_config(config)['max_height'], 720)
        self.assertNotIn('sentinel-secret', config.read_text())
        with patch.dict(os.environ, {'LVS_DOWNLOAD_CONFIG': str(config)}):
            self.assertEqual(m.config_path(), config)
            self.assertEqual(m.config_path(str(self.root / 'other')), self.root / 'other')

    def test_no_default_choice_in_noninteractive_install(self):
        with patch('sys.stdin.isatty', return_value=False):
            with self.assertRaises(m.IntakeError):
                m.configure(args('configure', '--config', str(self.root / 'cfg')))
        self.assertFalse((self.root / 'cfg').exists())

    def test_explicit_configure_repairs_corrupt_preference(self):
        config = self.root / 'config.json'; config.write_text('broken')
        m.configure(args('configure', '--backend', 'local', '--config', str(config)))
        self.assertEqual(m.read_config(config)['backend'], 'local')

    def test_tool_error_is_actionable_without_echoing_signed_url(self):
        result = subprocess.CompletedProcess([], 1, '', 'ERROR HTTP 403 https://cdn.example/?secret=hidden')
        with patch.object(m.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(m.IntakeError, 'HTTP 403') as error:
                m.run(['yt-dlp'], 'download')
        self.assertNotIn('secret', str(error.exception))

    def test_url_without_preference_does_not_touch_network_or_create_output(self):
        with patch.object(m, 'urlopen') as request, patch.object(m, 'binary') as check:
            with self.assertRaisesRegex(m.IntakeError, 'preference missing'):
                m.acquire(args('acquire', '--input', 'https://example.com/video', '--config', str(self.root / 'missing'),
                               '--output-dir', str(self.root / 'out')))
            request.assert_not_called()
            check.assert_not_called()
        self.assertFalse((self.root / 'out').exists())

    def test_local_file_bypasses_broken_preferences_and_downloader(self):
        source = self.root / '中文 素材.mp4'
        source.write_bytes(b'original')
        config = self.root / 'bad.json'
        config.write_text('invalid json')
        info = {'duration_ms': 1234, 'streams': [{'codec_type': 'video'}, {'codec_type': 'audio'}]}
        with patch.object(m, 'probe', return_value=info), patch.object(m, 'binary', return_value='ffprobe'), \
                patch.object(m, 'read_config') as read, patch.object(m, 'extract_snapany') as remote, \
                patch.object(m, 'ytdlp_command') as local:
            result = m.acquire(args('acquire', '--input', str(source), '--config', str(config), '--output-dir', str(self.root / 'out')))
            read.assert_not_called(); remote.assert_not_called(); local.assert_not_called()
        manifest = m.load(result['manifest'])
        self.assertEqual(manifest['media']['path'], str(source))
        self.assertEqual(manifest['analysis_status'], 'not_started')
        self.assertEqual(source.read_bytes(), b'original')
        self.assertEqual(m.verify(args('verify', '--manifest', result['manifest']))['status'], 'verified')
        source.write_bytes(b'changed!')
        with self.assertRaises(m.IntakeError):
            m.verify(args('verify', '--manifest', result['manifest']))

    def test_existing_output_is_never_overwritten(self):
        source = self.root / 'a.wav'; source.write_bytes(b'original')
        out = self.root / 'out'; out.mkdir(); (out / 'keep').write_text('keep')
        with patch.object(m, 'binary', return_value='ffprobe'), patch.object(m, 'probe') as probe:
            with self.assertRaises(FileExistsError):
                m.acquire(args('acquire', '--input', str(source), '--output-dir', str(out)))
            probe.assert_not_called()
        self.assertEqual((out / 'keep').read_text(), 'keep')

    def test_one_task_override_does_not_save_preferences_or_fallback(self):
        config = self.root / 'config.json'
        m.configure(args('configure', '--backend', 'remote', '--config', str(config)))
        original = config.read_bytes()
        with patch.object(m, 'binary', return_value='ffmpeg'), patch.object(m, 'ytdlp_command', return_value=['yt-dlp']), \
                patch.object(m, 'yt_download', side_effect=m.IntakeError('failed local')) as local, \
                patch.object(m, 'extract_snapany') as remote:
            with self.assertRaisesRegex(m.IntakeError, 'failed local'):
                m.acquire(args('acquire', '--input', 'https://example.com/video', '--backend', 'local',
                               '--config', str(config), '--output-dir', str(self.root / 'out')))
            local.assert_called_once(); remote.assert_not_called()
        self.assertEqual(config.read_bytes(), original)
        self.assertTrue((self.root / 'out' / 'failure.json').exists())
        self.assertFalse((self.root / 'out' / 'media-manifest.json').exists())

    def test_variant_selection_and_header_inheritance(self):
        data = {'medias': [{'media_type': 'image'}, {'media_type': 'video', 'headers': {'User-Agent': 'base', 'Referer': 'https://example.com'},
                'variants': [{'quality': 2160, 'video_url': 'https://cdn.example/4k'},
                             {'quality': 720, 'video_url': 'https://cdn.example/video', 'audio_url': 'https://cdn.example/audio',
                              'headers': {'user-agent': ''}}]}]}
        pick = m.select_snapany(data, 1080)
        self.assertEqual(pick['quality'], 720)
        self.assertEqual(pick['headers'], {'user-agent': '', 'referer': 'https://example.com'})
        self.assertTrue(pick['audio_url'].endswith('/audio'))
        with self.assertRaises(m.IntakeError):
            m.select_snapany(data, 480)

    def test_multiple_videos_require_selection_images_are_not_videos(self):
        data = {'medias': [{'media_type': 'image'}, {'media_type': 'video', 'resource_url': 'https://cdn.example/1'},
                           {'media_type': 'video', 'resource_url': 'https://cdn.example/2'}]}
        with self.assertRaises(m.IntakeError):
            m.select_snapany(data, 1080)
        self.assertEqual(m.select_snapany(data, 1080, 2)['video_url'], 'https://cdn.example/2')
        with self.assertRaises(m.IntakeError):
            m.select_snapany(data, 1080, 0)

    def test_bearer_only_goes_to_api_and_post_is_not_retried(self):
        out = self.root / 'out'; out.mkdir()
        opener = Mock()
        opener.open.return_value = Response(b'{"medias": []}')
        with patch.object(m, 'build_opener', return_value=opener), patch.dict(os.environ, {m.KEY_ENV: 'sentinel-secret'}):
            m.extract_snapany('https://example.com/video', out)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, m.SNAPANY_ENDPOINT)
        self.assertEqual(request.get_header('Authorization'), 'Bearer sentinel-secret')
        self.assertEqual(json.loads(request.data), {'url': 'https://example.com/video'})
        self.assertIsInstance(m.NoRedirect(), m.HTTPRedirectHandler)
        for file in out.iterdir():
            self.assertNotIn('sentinel-secret', file.read_text())
        other = self.root / 'failure'; other.mkdir()
        opener.open.reset_mock()
        opener.open.side_effect = HTTPError(m.SNAPANY_ENDPOINT, 429, 'rate limit', {}, None)
        with patch.object(m, 'build_opener', return_value=opener), patch.dict(os.environ, {m.KEY_ENV: 'sentinel-secret'}):
            with self.assertRaisesRegex(m.IntakeError, 'HTTP 429'):
                m.extract_snapany('https://example.com/video', other)
        opener.open.assert_called_once()
        self.assertTrue((other / 'snapany-request.json').exists())

    def test_cached_response_is_source_bound_and_needs_no_key_or_post(self):
        url = 'https://example.com/video'
        cache = self.root / 'cache.json'
        m.save(cache, {'schema_version': 1, 'endpoint': m.SNAPANY_ENDPOINT,
                      'source_url_sha256': m.hashlib.sha256(url.encode()).hexdigest(), 'response': {'medias': []}})
        out = self.root / 'out'; out.mkdir()
        with patch.object(m, 'build_opener') as api, patch.dict(os.environ, {}, clear=True):
            self.assertEqual(m.extract_snapany(url, out, cache)[1], 'cached')
            api.assert_not_called()
        bad = self.root / 'bad'; bad.mkdir()
        with self.assertRaises(m.IntakeError):
            m.extract_snapany('https://example.com/other', bad, cache)

    def test_download_stream_and_reject_truncation_and_playlist(self):
        with patch.object(m, 'urlopen', return_value=Response(b'media')) as request:
            path = self.root / 'download'
            m.download_http('https://cdn.example/media', path, {'user-agent': ''})
            self.assertEqual(path.read_bytes(), b'media')
            self.assertIsNone(request.call_args.args[0].get_header('Authorization'))
        with patch.object(m, 'urlopen', return_value=Response(b'short', {'Content-Length': '100'})):
            with self.assertRaises(m.IntakeError):
                m.download_http('https://cdn.example/media', self.root / 'short', {})
            self.assertFalse((self.root / 'short').exists())
            self.assertTrue((self.root / 'short.part').exists())
        with patch.object(m, 'urlopen', return_value=Response(b'#EXTM3U', {'Content-Type': 'application/vnd.apple.mpegurl'})):
            with self.assertRaises(m.IntakeError):
                m.download_http('https://cdn.example/list', self.root / 'list', {})

    def test_remote_silent_video_is_not_accepted_by_default(self):
        info = {'streams': [{'codec_type': 'video'}]}
        with self.assertRaises(m.IntakeError):
            m.validate_media(info, True, False)
        m.validate_media(info, True, True)
        with self.assertRaises(m.IntakeError):
            m.validate_media({'streams': [{'codec_type': 'audio'}]}, True, False)

    def test_ytdlp_preflight_rejects_playlist_before_download(self):
        with patch.object(m, 'run', return_value='{"_type": "playlist", "entries": []}') as run:
            with self.assertRaises(m.IntakeError):
                m.yt_download('https://example.com/playlist', self.root, 1080, ['yt-dlp'], '/bin/ffmpeg')
        run.assert_called_once()

    def test_ytdlp_command_is_argument_array_and_cookies_original_untouched(self):
        source_cookie = self.root / 'original.cookies'; source_cookie.write_text('cookie')
        out = self.root / 'out'; out.mkdir()
        def runner(command, *a, **kw):
            if '--dump-single-json' in command:
                return '{"id":"one","title":"one"}'
            if '--version' in command:
                return 'test-version'
            (out / 'media.mkv').write_bytes(b'media')
            (out / 'yt-dlp-files.private.jsonl').write_text(json.dumps(str(out / 'media.mkv')) + '\n', encoding='utf-8')
            return ''
        url = 'https://example.com/watch?id=x&list=y'
        with patch.object(m, 'run', side_effect=runner) as run:
            path, detail = m.yt_download(url, out, 720, ['yt-dlp'], '/bin/ffmpeg', source_cookie, 'node')
        self.assertEqual(path, out / 'media.mkv')
        cmd = run.call_args_list[1].args[0]
        self.assertEqual(cmd[-2:], ['--', url])
        self.assertIn('--ignore-config', cmd)
        self.assertIn('--no-write-auto-subs', cmd)
        self.assertIn('bv*[height<=720]+ba/b[height<=720]', cmd)
        self.assertNotIn(str(source_cookie), cmd)
        self.assertEqual(source_cookie.read_text(), 'cookie')
        self.assertFalse(detail['subtitles_downloaded'])

    def test_bad_headers_urls_and_invalid_source_are_rejected(self):
        for url in ['file:///etc/passwd', 'https://user:pw@example.com/video', 'https://example.com/\r\nheader']:
            with self.assertRaises(m.IntakeError):
                m.validate_url(url)
        with self.assertRaises(m.IntakeError):
            m.media_headers({'X-Test': 'x\r\ny'})
        with self.assertRaises(m.IntakeError):
            m.classify(str(self.root / 'missing.mp4'))


@unittest.skipUnless(os.environ.get('FFMPEG_TEST_BIN') and os.environ.get('FFPROBE_TEST_BIN'), 'Set explicit FFmpeg/ffprobe integration paths')
class MediaIntegration(unittest.TestCase):
    def test_real_file_probe_remote_split_merge_and_cached_reuse(self):
        ff = os.environ['FFMPEG_TEST_BIN']; fp = os.environ['FFPROBE_TEST_BIN']
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / 'video.mp4'; audio = root / 'audio.m4a'
            subprocess.run([ff, '-v', 'error', '-n', '-f', 'lavfi', '-i', 'color=c=blue:s=160x90:r=10:d=1',
                            '-an', '-c:v', 'mpeg4', str(video)], check=True)
            subprocess.run([ff, '-v', 'error', '-n', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1',
                            '-c:a', 'aac', str(audio)], check=True)
            url = 'https://example.com/video'; cache = root / 'cache.json'
            m.save(cache, {'schema_version': 1, 'endpoint': m.SNAPANY_ENDPOINT,
                          'source_url_sha256': m.hashlib.sha256(url.encode()).hexdigest(), 'response': {'id': 'fixture',
                          'medias': [{'media_type': 'video', 'variants': [{'quality': 90,
                          'video_url': 'https://cdn.example/video', 'audio_url': 'https://cdn.example/audio'}]}]}})
            def response(request, **kw):
                self.assertIsNone(request.get_header('Authorization'))
                return Response((audio if request.full_url.endswith('/audio') else video).read_bytes())
            with patch.object(m, 'urlopen', side_effect=response), patch.object(m, 'build_opener') as api:
                result = m.acquire(args('acquire', '--input', url, '--backend', 'remote', '--config', str(root / 'config'),
                    '--snapany-response', str(cache), '--ffmpeg', ff, '--ffprobe', fp, '--output-dir', str(root / 'remote')))
                api.assert_not_called()
            manifest = m.load(result['manifest'])
            self.assertEqual(manifest['status'], 'ready')
            self.assertEqual({s['codec_type'] for s in manifest['media']['streams']}, {'audio', 'video'})
            self.assertGreaterEqual(manifest['media']['duration_ms'], 1000)
            m.verify(args('verify', '--manifest', result['manifest']))
            local = m.acquire(args('acquire', '--input', result['media_path'], '--ffprobe', fp,
                                   '--output-dir', str(root / 'local')))
            self.assertEqual(m.load(local['manifest'])['media']['sha256'], manifest['media']['sha256'])
            subprocess.run([ff, '-v', 'error', '-i', result['media_path'], '-f', 'null', '-'], check=True)


if __name__ == '__main__':
    unittest.main()
