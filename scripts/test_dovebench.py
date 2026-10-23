"""Offline cache integrity and reference-isolation checks with synthetic data."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import dovebench as d


class DoveBenchTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'data').mkdir()
        data=[dict(video_id='sc2/test',youtube_id='synthetic00',idx=0,start=0,end=4,zh_text='参考',en_text='Reference')]
        (self.root/'data/dovebench.jsonl').write_text(json.dumps(data[0])+'\n',encoding='utf-8')
        d.write(self.root/'catalog.json',[dict(video_id='sc2/test',youtube_id='synthetic00',segments=1,start=0,end=4)])
        d.write(self.root/'cache.json',dict(data_sha256=d.sha(self.root/'data/dovebench.jsonl'),catalog_sha256=d.sha(self.root/'catalog.json'),rows=1,videos=1))
        (self.root/'input.srt').write_text('1\n00:00:00,000 --> 00:00:04,000\n候选\n',encoding='utf-8')

    def tearDown(self):self.tmp.cleanup()

    def test_freeze_never_loads_reference_and_does_not_overwrite(self):
        args=SimpleNamespace(hypothesis=self.root/'input.srt',video_id='sc2/test',output=self.root/'run',prior_exposure='unknown',exposure_note='',hypothesis_status='draft')
        with patch.object(d,'load_cache',side_effect=AssertionError('Reference accessed')):
            d.freeze(args,self.root)
        self.assertFalse(d.read(self.root/'run/freeze.json')['reference_text_read_by_freeze'])
        with self.assertRaises(FileExistsError):d.freeze(args,self.root)
        (self.root/'run/hypothesis.srt').write_text('changed',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'Frozen hypothesis modified'):
            d.score(SimpleNamespace(run=self.root/'run'),self.root)

    def test_corrupted_cache_is_rejected(self):
        d.load_cache(self.root)
        with (self.root/'data/dovebench.jsonl').open('a',encoding='utf-8') as f:f.write(' ')
        with self.assertRaisesRegex(ValueError,'dataset changed'):d.load_cache(self.root)

    def test_invalid_srt_is_not_silently_truncated(self):
        with (self.root/'input.srt').open('a',encoding='utf-8') as f:f.write('\n2\nbad time\ntext')
        with self.assertRaises(ValueError):d.parse_srt(self.root/'input.srt')

    def test_empty_aligned_english_does_not_drop_chinese_reference(self):
        p=self.root/'data/dovebench.jsonl';r=json.loads(p.read_text(encoding='utf-8'));r['en_text']=''
        p.write_text(json.dumps(r)+'\n',encoding='utf-8')
        m=d.read(self.root/'cache.json');m['data_sha256']=d.sha(p)
        (self.root/'cache.json').write_text(json.dumps(m),encoding='utf-8')
        self.assertEqual(len(d.load_cache(self.root)[1]['sc2/test']),1)

    def test_reference_diagnostics_require_score_and_preserve_baseline(self):
        args=SimpleNamespace(hypothesis=self.root/'input.srt',video_id='sc2/test',output=self.root/'run',prior_exposure='known',exposure_note='',hypothesis_status='draft')
        d.freeze(args,self.root)
        annotations=dict(video_id='sc2/test',issues=[dict(idx=0,category='polarity_conflict',status='suspected',note='Synthetic reference polarity conflict; needs listening',reviewer='synthetic test')])
        d.write(self.root/'annotations.json',annotations)
        request=SimpleNamespace(run=self.root/'run',annotations=self.root/'annotations.json',output=self.root/'diagnostic.json')
        with self.assertRaises(FileNotFoundError):d.reference_issues(request,self.root)
        (self.root/'run/scores').mkdir();f=d.read(self.root/'run/freeze.json')
        d.write(self.root/'run/scores/metrics.json',dict(hypothesis_sha256=f['hypothesis_sha256'],data_sha256=f['data_sha256']))
        protected=[self.root/'run/hypothesis.srt',self.root/'data/dovebench.jsonl',self.root/'run/scores/metrics.json']
        before=[d.sha(p) for p in protected];d.reference_issues(request,self.root)
        self.assertEqual(before,[d.sha(p) for p in protected]);self.assertEqual(d.read(request.output)['issues'][0]['reference']['idx'],0)
        with self.assertRaises(FileExistsError):d.reference_issues(request,self.root)

    def test_diagnostics_reject_wrong_video_or_missing_evidence(self):
        d.freeze(SimpleNamespace(hypothesis=self.root/'input.srt',video_id='sc2/test',output=self.root/'run',prior_exposure='known',exposure_note='',hypothesis_status='draft'),self.root)
        (self.root/'run/scores').mkdir();f=d.read(self.root/'run/freeze.json')
        d.write(self.root/'run/scores/metrics.json',dict(hypothesis_sha256=f['hypothesis_sha256'],data_sha256=f['data_sha256']))
        path=self.root/'a.json';request=SimpleNamespace(run=self.root/'run',annotations=path,output=self.root/'out.json')
        d.write(path,dict(video_id='other',issues=[]))
        with self.assertRaisesRegex(ValueError,'video differs'):d.reference_issues(request,self.root)
        path.write_text(json.dumps(dict(video_id='sc2/test',issues=[dict(idx=0,category='polarity_conflict',status='confirmed',reviewer='test',note='assertion only')])),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'actual evidence'):d.reference_issues(request,self.root)


if __name__=='__main__':unittest.main()
