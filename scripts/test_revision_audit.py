import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from revision_audit import compare


def cue(sid, source, target, start=0, end=2000, parents=None):
    d=dict(id=sid,source=source,target=target,draft=target,start_ms=start,end_ms=end)
    if parents is not None:d['parent_ids']=parents
    return d


def doc(*ss):return dict(meta=dict(video_id='synthetic',target_language='zh-Hans'),segments=list(ss))


class RevisionAuditTests(unittest.TestCase):
    def test_timing_only_never_requests_rewording_or_mutates(self):
        a=doc(cue('a','Stay.','别走'));b=copy.deepcopy(a);b['segments'][0]['end_ms']=2500
        orig=copy.deepcopy((a,b));r=compare(a,b)
        self.assertEqual((a,b),orig);self.assertEqual(r['summary']['review_groups'],0)
        self.assertTrue(r['items'][0]['timing_changed'])

    def test_split_has_no_false_text_loss(self):
        a=doc(cue('a','First wait. Then leave.','先等等 然后再走'))
        b=doc(cue('a1','First wait.','先等等',0,1000,['a']),cue('a2','Then leave.','然后再走',1000,2000,['a']))
        r=compare(a,b);self.assertFalse(r['structural_errors']);self.assertEqual(len(r['items']),1)
        x=r['items'][0];self.assertEqual(x['retention_ratio'],1);self.assertFalse(x['text_changed']);self.assertFalse(x['source_changed'])

    def test_merge_is_one_group_not_duplicate_parent_counts(self):
        a=doc(cue('a','No.','不',0,100),cue('b','Wait.','等一下',100,1500))
        b=doc(cue('ab','No. Wait.','不 等一下',0,1500,['a','b']))
        r=compare(a,b);self.assertEqual(r['summary']['matched_groups'],1)
        self.assertEqual(r['items'][0]['before_chars'],4)

    def test_sibling_revisions_use_common_ancestor(self):
        a=doc(cue('a1','One.','一',0,1000,['a']),cue('a2','Two.','二',1000,2000,['a']))
        b=doc(cue('a3','One. Two.','一 二',0,2000,['a']))
        r=compare(a,b);self.assertFalse(r['structural_errors']);self.assertEqual(len(r['items']),1)

    def test_missing_cue_not_hidden_by_positional_mapping(self):
        a=doc(cue('a','First.','首先'),cue('b','Then.','然后',2000,3000))
        b=doc(cue('a','First.','首先'))
        r=compare(a,b);self.assertEqual(r['structural_errors'][0]['code'],'unmapped_before')

    def test_blank_after_does_not_fall_back_to_draft(self):
        a=doc(cue('a','No.','不'));b=copy.deepcopy(a);b['segments'][0]['target']=''
        r=compare(a,b);self.assertTrue(any(e['code']=='empty_translation' for e in r['structural_errors']))
        self.assertFalse(compare(a,b,after_field='draft')['structural_errors'])

    def test_shortening_and_temporal_scope_trigger_review_not_verdict(self):
        a=doc(cue('a','It might arrive soon, but only after two minutes.','可能快到了 但至少还要两分钟'))
        b=doc(cue('a','It might arrive soon, but only after two minutes.','正在来'))
        x=compare(a,b)['items'][0]
        self.assertIn('shortened_review_information_and_tone',x['review_reasons'])
        self.assertEqual(x['semantic_review'],'pending');self.assertIn('certainty',x['source_attention'])
        self.assertIn('stage_time',x['source_attention']);self.assertIn('quantity',x['source_attention'])
        self.assertNotIn('semantic_error',x)

    def test_source_change_even_if_target_unchanged(self):
        a=doc(cue('a','Maybe.','也许'));b=doc(cue('a','Certainly.','也许'))
        self.assertTrue(compare(a,b)['items'][0]['source_changed'])

    def test_invalid_identity_and_ambiguous_fields_rejected(self):
        a=doc(cue('a','X','甲'))
        with self.assertRaises(ValueError):compare(a,doc(cue('a','X','甲'),cue('a','Y','乙')))
        b=copy.deepcopy(a);b['meta']['video_id']='another'
        with self.assertRaises(ValueError):compare(a,b)
        b=copy.deepcopy(a);del b['segments'][0]['target']
        with self.assertRaises(ValueError):compare(a,b)

    def test_cli_preserves_input_hashes_and_refuses_output_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);p=r/'in.json';p.write_text(json.dumps(doc(cue('a','Hello','你好'))),encoding='utf8')
            cmd=[sys.executable,'-B',str(Path(__file__).with_name('revision_audit.py')),'--before',str(p),'--after',str(p),'--output',str(r/'out.json')]
            a=subprocess.run(cmd,capture_output=True,text=True);self.assertEqual(a.returncode,0,a.stderr)
            result=json.loads((r/'out.json').read_text(encoding='utf8'))
            self.assertEqual(result['input_sha256']['before'],result['input_sha256']['after'])
            content=(r/'out.json').read_bytes();b=subprocess.run(cmd,capture_output=True)
            self.assertNotEqual(b.returncode,0);self.assertEqual(content,(r/'out.json').read_bytes())


if __name__=='__main__':unittest.main()
