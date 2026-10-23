"""Synthetic provider tests; no network, uploads, or paid jobs."""
import argparse
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import asr_backend as backend
from asr_credentials import dpapi


class AsrTest(unittest.TestCase):
    def config(self):
        return json.loads(backend.DEFAULT_CONFIG.read_text(encoding='utf-8'))['backends']['assemblyai']

    def test_optional_selection_and_model_override(self):
        args = argparse.Namespace(config=backend.DEFAULT_CONFIG, backend=None, model=None, language=None)
        with self.assertRaises(ValueError):
            backend.config_for(args)
        args.backend, args.model, args.language = 'assemblyai', ['future-model'], 'en'
        _, cfg = backend.config_for(args)
        self.assertEqual(cfg['speech_models'], ['future-model'])
        self.assertFalse(cfg['language_detection'])

    @patch('asr_backend.read_key', return_value='synthetic-test-only')
    def test_auth_payload_and_host_restriction(self, _):
        cfg = self.config()
        client = backend.AssemblyAI(cfg)
        with patch.object(client, 'request', return_value={}) as request:
            self.assertEqual(client.check_auth()['submitted_jobs'], 0)
            request.assert_called_once_with('GET', '/v2/transcript?limit=1')
        with patch.object(client, 'request', return_value={}) as request:
            client.submit('https://example.com/test.wav')
            body = request.call_args.kwargs['data']
            self.assertNotIn('api_key_env', body)
            self.assertNotIn('credential_name', body)
            self.assertEqual(body['speech_models'], cfg['speech_models'])
        cfg['base_url'] = 'https://example.com'
        with self.assertRaises(ValueError):
            backend.AssemblyAI(cfg)

    def test_millisecond_conversion_and_speaker(self):
        transcript = {'status': 'completed', 'id': 'synthetic-id', 'speech_model_used': 'test', 'language_code': 'en'}
        sentences = {'sentences': [{'start': 125, 'end': 2200, 'text': 'Hello.', 'confidence': .95,
                     'words': [{'start': 125, 'end': 2200, 'text': 'Hello.', 'confidence': .95, 'speaker': 'A'}]}]}
        job = {'offset_ms': 3600000, 'backend': 'assemblyai', 'source': {'sha256': 'synthetic'}}
        r = backend.normalize(transcript, sentences, job)['segments'][0]
        self.assertEqual((r['start_ms'], r['end_ms']), (3600125, 3602200))
        self.assertEqual(r['asr_words'][0]['start'], .125)
        self.assertEqual(r['asr_words_offset_ms'], 3600000)
        self.assertEqual(r['speaker'], 'A')
        self.assertEqual(r['review_status'], 'pending')
        with self.assertRaises(ValueError):
            backend.normalize(transcript, {'sentences': []}, job)

    @patch('asr_backend.read_key', return_value='synthetic-test-only')
    def test_uncertain_submit_never_reposts(self, _):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'job.json'
            args = argparse.Namespace(config=backend.DEFAULT_CONFIG, backend='assemblyai', model=None,
                language=None, file=None, audio_url='https://example.com/test.wav', job=path, offset_ms=0)
            with patch.object(backend.AssemblyAI, 'submit', side_effect=backend.ApiError('test timeout')) as submit:
                with self.assertRaises(backend.ApiError):
                    backend.submit_job(args)
                self.assertEqual(backend.load(path)['phase'], 'submission_uncertain')
                with self.assertRaises(ValueError):
                    backend.submit_job(args)
                self.assertEqual(submit.call_count, 1)
            self.assertNotIn('synthetic-test-only', path.read_text(encoding='utf-8'))

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI only')
    def test_dpapi_round_trip(self):
        sample = b'synthetic-credential-test-only'
        encrypted = dpapi(sample)
        self.assertNotIn(sample, encrypted)
        self.assertEqual(dpapi(encrypted, decrypt=True), sample)

    @patch('asr_backend.read_key', return_value='synthetic-test-only')
    def test_resume_completed_job_exports_pending_source(self, _):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'job.json'
            job = {'schema_version': 1, 'backend': 'assemblyai', 'config': self.config(),
                   'source': {'sha256': 'synthetic'}, 'phase': 'queued', 'transcript_id': 'synthetic-id', 'offset_ms': 0}
            backend.save(path, job)
            transcript = {'id': 'synthetic-id', 'status': 'completed', 'language_code': 'en',
                          'audio_url': 'https://example.com/private-signed-media', 'speech_model_used': 'test'}
            sentences = {'sentences': [{'start': 0, 'end': 1500, 'text': 'Hello.', 'words': []}]}
            args = argparse.Namespace(job=path, wait_seconds=0, output_dir=Path(directory)/'result')
            with patch.object(backend.AssemblyAI, 'transcript', return_value=transcript), \
                 patch.object(backend.AssemblyAI, 'sentences', return_value=sentences), \
                 patch.object(backend.AssemblyAI, 'submit') as submit:
                backend.status_job(args)
                submit.assert_not_called()
            self.assertEqual(backend.load(path)['phase'], 'completed')
            doc = backend.load(args.output_dir/'source.json')
            self.assertEqual(doc['segments'][0]['source'], 'Hello.')
            self.assertEqual(doc['segments'][0]['review_status'], 'pending')
            self.assertNotIn('audio_url', backend.load(args.output_dir/'assemblyai.transcript.json'))


if __name__ == '__main__':
    unittest.main()
