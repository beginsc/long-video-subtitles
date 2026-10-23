"""Regression tests for unresolved source evidence and post-QA artifact changes."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
import subtitle_workbench as w
import review_evidence as e


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.r=w.row('s1',0,4000,'Void rays defeat... void rays?')
        self.r.update(draft='虚空打……虚空？',vision_status='skipped',visual_cues=[],source_checks=[])
        self.doc=dict(schema_version=1,meta={'workflow':'vidove-inspired-v1','quality_contract':'evidence-v2'},segments=[self.r])

    def tearDown(self):self.tmp.cleanup()

    def apply(self,stage,change):
        packet=dict(batch_id='b1',segments=[copy.deepcopy(self.r)],context_before=[],context_after=[],project_meta=self.doc['meta'])
        packet['input_digest']=w.digest(packet)
        result=dict(batch_id='b1',input_digest=packet['input_digest'],segments=[dict(id='s1',**change)])
        for name,value in [('input',self.doc),('batch',packet),('result',result)]:
            (self.root/name).write_text(json.dumps(value),encoding='utf-8')
        output=self.root/f'output-{stage}.json'
        w.apply_result(SimpleNamespace(input=self.root/'input',batch=self.root/'batch',result=self.root/'result',output=output,stage=stage))
        self.doc=w.load(output);self.r=self.doc['segments'][0]

    def test_open_asr_concern_rejects_pass_and_asr_consensus_resolution(self):
        self.r['source_checks']=[dict(id='a1',kind='asr',status='open',reason='Ambiguous repeated object')]
        with self.assertRaisesRegex(ValueError,'Open source'):
            self.apply('proofread',dict(proofreading_status='PASS',suggestions=[],proofreading_note='Same words from two ASR models.'))
        self.r['source_checks'][0].update(status='resolved',resolution=dict(method='asr_consensus',evidence=['asr2.json'],note='They agree'))
        with self.assertRaises(ValueError):w.structural(self.doc)

    def test_unclear_cannot_be_approved_with_generic_editor_note(self):
        self.apply('proofread',dict(proofreading_status='UNCLEAR',suggestions=['Listen to repeated object.'],proofreading_note='Audio needed'))
        change=dict(target='虚空打……虚空？',review_status='reviewed',review_note='Checked, looks fine.',issues=[])
        with self.assertRaisesRegex(ValueError,'UNCLEAR'):
            self.apply('edit',change)
        change['uncertainty_resolution']=dict(method='audio_review',evidence=['synthetic-listening-record.json'],note='Synthetic test evidence: explicit self-correction')
        self.apply('edit',change)
        self.assertEqual(self.r['review_status'],'reviewed')
        old=self.r['editing_digest'];self.r['uncertainty_resolution']['note']='changed'
        self.assertNotEqual(old,w.editing_digest(self.r))

    def test_source_checks_are_required_and_invalidate_proofreading(self):
        old=w.proofreading_digest(self.r,self.doc['meta'])
        self.r['source_checks']=[dict(id='a1',kind='meaning',status='open',reason='Who attacked whom?')]
        self.assertNotEqual(old,w.proofreading_digest(self.r,self.doc['meta']))
        del self.r['source_checks']
        with self.assertRaises(ValueError):w.structural(self.doc)

    def test_unresolved_draft_stays_exportable_but_final_is_blocked(self):
        self.r['source_checks']=[dict(id='a1',kind='visual',status='open',reason='Oracle or Void Ray?')]
        args=self.options();w.save(self.root/'doc.json',self.doc)
        with self.assertRaises(ValueError):w.export(args)
        args.draft=True;w.export(args)
        self.assertTrue((self.root/'export/draft.srt').exists())
        w.verify_export(SimpleNamespace(output_dir=self.root/'export'))
        (self.root/'export/draft.srt').write_text('modified after QA',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'changed after QA'):
            w.verify_export(SimpleNamespace(output_dir=self.root/'export'))

    def options(self):
        return SimpleNamespace(input=self.root/'doc.json',output_dir=self.root/'export',baseline=None,duration_ms=None,
            bilingual=True,min_seconds=.8,max_seconds=7,max_width=42,max_lines=2,bilingual_lines=2,bilingual_total_lines=4,
            target_cps=12,source_cps=20,draft=False,format='srt',source_first=False)

    def test_bilingual_auto_wrapping_counts_both_tracks(self):
        self.r.update(source='long English sentence '*10,target='中'*35)
        report=w.check(self.doc,self.options())
        self.assertIn('bilingual_total_layout',{f['code'] for f in report['findings']})


if __name__=='__main__':unittest.main()
