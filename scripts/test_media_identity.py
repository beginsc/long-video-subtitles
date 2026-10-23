import json
from pathlib import Path
import tempfile
import unittest
from media_identity import compare, english_reference, sha, sources


class MediaIdentityTest(unittest.TestCase):
    def corpus(self, prefix):
        return [dict(start=t, end=t + 70, text=' '.join(f'{prefix}{i}' for i in range(50))) for t in [0, 455, 910]]

    def test_equal_duration_wrong_content_rejected(self):
        self.assertEqual(compare(self.corpus('source'), self.corpus('different'), 1000)['status'], 'requires_identity_review')

    def test_partial_transcript_not_certified(self):
        self.assertEqual(compare(self.corpus('same')[:1], self.corpus('same'), 1000)['status'], 'insufficient_content')

    def test_consistent_numeric_only(self):
        result = compare(self.corpus('secretword'), self.corpus('secretword'), 1000)
        self.assertEqual(result['status'], 'consistent_fingerprint')
        self.assertNotIn('secretword', json.dumps(result))

    def test_cache_hash_and_english_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); (p/'data').mkdir()
            data = p/'data/dovebench.jsonl'
            data.write_text(json.dumps(dict(video_id='a', youtube_id='abcdefghijk', start=0, end=2,
                                            en_text='visible english words', zh_text='DO_NOT_DISCLOSE'))+'\n', encoding='utf8')
            (p/'cache.json').write_text(json.dumps(dict(data_sha256=sha(data))), encoding='utf8')
            self.assertNotIn('DO_NOT_DISCLOSE', json.dumps(english_reference(p, 'a')))
            data.write_text('{}\n', encoding='utf8')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                english_reference(p, 'a')

    def test_invalid_source_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'s.json'
            p.write_text(json.dumps(dict(segments=[dict(start_ms=0, end_ms=999999, source='x')])), encoding='utf8')
            with self.assertRaisesRegex(ValueError, 'absolute media times'):
                sources([p], 10)


if __name__ == '__main__':
    unittest.main()
