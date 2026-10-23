import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest
import benchmark_neural as b


class NeuralTests(unittest.TestCase):
    def data(self):
        rows=[dict(idx=0,start=0,end=1,reference='甲',hypothesis=''),dict(idx=1,start=1,end=2,reference='乙',hypothesis='乙')]
        return dict(groups=dict(AS=copy.deepcopy(rows),t=copy.deepcopy(rows)),coverage=dict(reference_cues=2))

    def test_empty_candidates_kept_and_count_enforced(self):
        d=self.data();b.validate_pairs(d)
        d['groups']['AS'].pop(0)
        with self.assertRaises(ValueError):b.validate_pairs(d)

    def test_alignment_identity_checked(self):
        d=self.data();d['groups']['t'][1]['reference']='丙'
        with self.assertRaises(ValueError):b.validate_pairs(d)

    def test_real_public_tokenizer_path_checks_truncation(self):
        class Tokenizer:
            def tokenize(self,x):return list(x)
        self.assertEqual(b.check_lengths(self.data()['groups']['AS'],Tokenizer(),10),5)
        with self.assertRaises(ValueError):b.check_lengths(self.data()['groups']['AS'],Tokenizer(),4)

    def test_freeze_identity_and_comparison_are_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'scores').mkdir()
            (root/'hypothesis.srt').write_text('1\n00:00:00,000 --> 00:00:02,000\n甲乙\n',encoding='utf8')
            (root/'scores/reference.zh.srt').write_text('1\n00:00:00,000 --> 00:00:01,000\n甲\n\n2\n00:00:01,000 --> 00:00:02,000\n乙\n',encoding='utf8')
            common=dict(hypothesis_sha256=b.sha(root/'hypothesis.srt'),data_sha256='test')
            b.write(root/'freeze.json',common)
            b.write(root/'scores/metrics.json',dict(common,reference_sha256=b.sha(root/'scores/reference.zh.srt')))
            d=self.data();d.update(hypothesis_sha256=common['hypothesis_sha256'],reference_sha256=b.sha(root/'scores/reference.zh.srt'))
            b.write(root/'pairs.json',d)
            result=copy.deepcopy(d);result.update(status='completed',pairs_sha256=b.sha(root/'pairs.json'))
            result['groups']={mode:dict(pairs=[dict(x,BLEURT=0.1,BERT_F1=0.2) for x in rows]) for mode,rows in d['groups'].items()}
            b.write(root/'metrics.json',result)
            b.compare(root,root/'pairs.json',root/'metrics.json',root/'comparison.csv')
            with (root/'comparison.csv').open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),2);self.assertEqual(rows[0]['AS_text'],'')
            self.assertEqual(rows[0]['actual_complete_cues'],'甲乙');self.assertEqual(rows[1]['candidate_cue_numbers'],'1')
            (root/'hypothesis.srt').write_text('changed',encoding='utf8')
            with self.assertRaises(ValueError):b.verify_run(root)


if __name__=='__main__':unittest.main()
