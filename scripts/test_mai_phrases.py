import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import asr_workflow as a


class MaiPhraseTests(unittest.TestCase):
    def test_existing_request_keeps_original_style_timing_and_locale(self):
        self.assertEqual(a.mai_definition({'language': 'en'}), {
            'enhancedMode': {'enabled': True, 'model': 'MAI-Transcribe-2',
                             'modelOptions': {'timestamps': 'word', 'transcribeStyle': 'verbatim'}},
            'locales': ['en']})

    def test_phrase_list_is_top_level_and_deduplicated(self):
        definition = a.mai_definition({'phrases': [' Widow  mine ', 'WIDOW MINE', 'PartinG', '雷神']})
        self.assertEqual(definition['phraseList'], {'phrases': ['Widow mine', 'PartinG', '雷神']})
        self.assertNotIn('locales', definition)
        self.assertNotIn('phraseList', definition['enhancedMode'])

    def test_empty_list_preserves_no_bias_baseline(self):
        self.assertEqual(a.mai_definition({}), a.mai_definition({'phrases': []}))

    def test_bad_phrases_are_not_silently_coerced(self):
        for values in ('Widow mine', {}, [None], [1], [''], ['  '], ['a\nb'], ['a\x00b']):
            with self.subTest(values=values), self.assertRaises(a.io.IntakeError):
                a.validate_phrases(values)

    def test_input_snapshot_hashes_original_bytes_and_rejects_glossary(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)/'phrases.json'
            p.write_text(json.dumps({'phrases': ['Widow mine', 'widow mine']}), encoding='utf-8-sig')
            phrases, source = a.load_phrases(p)
            self.assertEqual(phrases, ['Widow mine'])
            self.assertEqual(source['sha256'], a.io.sha256(p))
            self.assertEqual(source['path'], str(p.resolve()))
            p.write_text('{"phrases": [], "target": "地雷"}', encoding='utf-8')
            with self.assertRaises(a.io.IntakeError):
                a.load_phrases(p)

    def test_prepare_only_needs_no_credentials_and_cannot_submit_or_recover(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'audio.wav'; source.write_bytes(b'test audio')
            phrases = root/'phrases.json'; phrases.write_text('["Widow mine"]', encoding='utf-8')
            args = a.parser().parse_args(['transcribe', '--backend', 'remote', '--config', str(root/'absent.json'),
                                         '--file', str(source), '--output-dir', str(root/'prepared'),
                                         '--phrases-file', str(phrases), '--prepare-only'])
            with patch.dict(a.os.environ, {}, clear=True), patch.object(a.io, 'binary', side_effect=lambda x:x) as binary, \
                    patch.object(a.io, 'probe', return_value={'duration_ms': 1000}), \
                    patch.object(a.io, 'validate_media'), patch.object(a, 'transcribe_mai') as submit:
                result = a.transcribe(args)
                submit.assert_not_called()
                self.assertNotIn('curl', [call.args[0] for call in binary.call_args_list])
            self.assertEqual(result['status'], 'prepared_only')
            self.assertFalse(result['network_called'])
            out = root/'prepared'
            request = a.io.load(out/'request.json')
            self.assertEqual(request['phrases'], ['Widow mine'])
            self.assertEqual(request['phrase_source']['sha256'], a.io.sha256(phrases))
            self.assertFalse((out/'provider-receipt.json').exists())
            with self.assertRaises(a.io.IntakeError):
                a.recover(SimpleNamespace(output_dir=str(out)))

    def test_options_rejected_for_other_backends_before_media_or_network(self):
        for option in ('--prepare-only', '--phrases-file'):
            command = ['transcribe', '--backend', 'local', '--file', 'absent.wav', '--output-dir', 'unused']
            command += [option] + (['absent.json'] if option == '--phrases-file' else [])
            args = a.parser().parse_args(command)
            with patch.object(a, 'choose', return_value=('whisperx', {'driver': 'cli'})), \
                    self.assertRaisesRegex(a.io.IntakeError, 'require the MAI backend'):
                a.transcribe(args)

    def test_real_upload_builder_carries_bias_once_without_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            audio = out/'source.wav'; audio.write_bytes(b'wave fixture')
            request = {'media': {'path': str(audio), 'sha256': a.io.sha256(audio)},
                       'spec': {'endpoint': 'https://westus2.api.cognitive.microsoft.com', 'key_env': 'TEST_MAI_KEY'},
                       'phrases': ['Widow mine'], 'language': 'en'}
            captured = []
            def fake_curl(command, **kwargs):
                body = (out/'upload.multipart.private').read_text(encoding='utf-8')
                definition = json.loads(body.split('\n\n', 1)[1].split('\n', 1)[0])
                captured.append(definition)
                self.assertEqual(command[command.index('--retry')+1], '0')
                self.assertNotIn('test-key', ' '.join(command))
                Path(command[command.index('--output')+1]).write_text('{"phrases": []}', encoding='utf-8')
                return SimpleNamespace(returncode=0, stdout='200')
            with patch.dict(a.os.environ, {'TEST_MAI_KEY': 'test-key'}), \
                    patch.object(a.io, 'binary', return_value='curl'), patch.object(a.subprocess, 'run', side_effect=fake_curl) as call:
                raw, canonical = a.transcribe_mai(request, out)
            self.assertEqual(call.call_count, 1)
            self.assertEqual(captured[0]['phraseList']['phrases'], ['Widow mine'])
            self.assertEqual(captured[0], a.io.load(out/'mai-definition.json'))
            self.assertEqual(canonical['segments'], [])
            self.assertTrue(raw.exists())
            self.assertFalse((out/'upload.multipart.private').exists())

    def test_failed_request_is_not_retried_and_retains_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp); source = out/'audio.wav'; source.write_bytes(b'fixture')
            request = {'media': {'path': str(source), 'sha256': a.io.sha256(source)},
                       'spec': {'endpoint': 'https://westus2.api.cognitive.microsoft.com', 'key_env': 'TEST_MAI_KEY'},
                       'phrases': ['PartinG']}
            with patch.dict(a.os.environ, {'TEST_MAI_KEY': 'test-key'}), patch.object(a.io, 'binary', return_value='curl'), \
                    patch.object(a.subprocess, 'run', return_value=SimpleNamespace(returncode=28, stdout='000')) as call:
                with self.assertRaises(a.io.IntakeError):
                    a.transcribe_mai(request, out)
            self.assertEqual(call.call_count, 1)
            self.assertTrue((out/'mai-definition.json').exists())
            self.assertTrue((out/'submit-intent.json').exists())


if __name__ == '__main__':
    unittest.main()
