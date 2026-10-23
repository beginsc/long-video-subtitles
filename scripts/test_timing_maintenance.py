import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import subtitle_timing
from subtitle_workbench import row


class TimingTests(unittest.TestCase):
    def test_import_modes_preserve_fixed_axis_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);tool=Path(__file__).with_name('subtitle_workbench.py')
            (root/'in.srt').write_text('1\n00:00:00,000 --> 00:00:01,000\nHello\n',encoding='utf8')
            (root/'asr.json').write_text(json.dumps({'segments':[{'start':0,'end':1,'text':'Hello'}]}),encoding='utf8')
            for name,file,extra,expected in [('srt','in.srt',[],'fixed'),('asr','asr.json',[],'rebuild'),('asr','asr.json',['--timing-mode','fixed'],'fixed')]:
                out=root/(name+expected+'.json')
                p=subprocess.run([sys.executable,'-B',str(tool),'import-'+name,str(root/file),str(out),*extra],capture_output=True,text=True)
                self.assertEqual(p.returncode,0,p.stderr)
                self.assertEqual(json.loads(out.read_text())['meta']['timing_mode'],expected)

    def doc(self):
        rows=[row('a',1000,1060,'No.'),row('b',1300,2500,'Stay here.')]
        for r,t in zip(rows,['不','待在这里']):
            r.update(target=t,draft=t,review_status='reviewed',review_note='Text reviewed')
        return dict(schema_version=1,meta=dict(duration_ms=3000,timing_mode='rebuild'),segments=rows)

    def test_never_mutates_and_no_overlap_extension(self):
        d=self.doc();before=copy.deepcopy(d);p=subtitle_timing.plan(d)
        self.assertEqual(d,before)
        self.assertEqual(p['items'][0]['action'],'semantic_resegment_review')
        self.assertNotIn('proposed_end_ms',p['items'][0])

    def test_extension_is_bounded_and_scene_blocks(self):
        d=self.doc();d['segments'][0]['end_ms']=1600;d['segments'][1]['start_ms']=2000
        p=subtitle_timing.plan(d);self.assertEqual(p['items'][0]['proposed_end_ms'],1800)
        d['meta']['scene_boundaries_ms']=[1700]
        self.assertNotIn('proposed_end_ms',subtitle_timing.plan(d)['items'][0])

    def test_fixed_unknown_duration_and_other_speaker(self):
        d=self.doc();d['meta']['timing_mode']='fixed'
        self.assertEqual(subtitle_timing.plan(d)['items'][0]['action'],'fixed_axis_review')
        d['meta']['timing_mode']='rebuild';d['meta'].pop('duration_ms')
        d['segments'][0]['speaker']='A';d['segments'][1]['speaker']='B'
        p=subtitle_timing.plan(d)['items'][0]
        self.assertNotIn('proposed_end_ms',p);self.assertEqual(p['candidate_neighbor_ids'],[])

    def test_flash_blocks_final_but_draft_preserves_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);src=root/'source.json';src.write_text(json.dumps(self.doc()),encoding='utf8')
            tool=Path(__file__).with_name('subtitle_workbench.py')
            cmd=[sys.executable,'-B',str(tool),'export',str(src)]
            p=subprocess.run(cmd+[str(root/'final'),'--baseline',str(src)],capture_output=True,text=True)
            self.assertNotEqual(p.returncode,0);self.assertFalse((root/'final').exists())
            p=subprocess.run(cmd+[str(root/'draft'),'--draft'],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            qa=json.loads((root/'draft/qa.json').read_text());self.assertIn('flash_duration',qa['summary']['codes'])
            self.assertIn('不',(root/'draft/draft.srt').read_text(encoding='utf8'))


if __name__=='__main__':unittest.main()
