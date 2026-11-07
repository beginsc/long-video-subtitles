import copy
import unittest
from layout import wrap, quality, width
from tail_proposals import propose
from unified_cues import compose
from test_unified_cues import reference, units_for, word
from display_checks import screen, resolve_profile


class LayoutTailTests(unittest.TestCase):
    def test_balances_without_orphan_or_phrase_split(self):
        texts = ['学生们今天终于组好了新的电子乐器', '这个词组需要留在同一行 The Northern Lights 是乐队名', '春天的消息让大家突然想起文学研究部']
        for text in texts:
            p = ['电子乐器', 'The Northern Lights', '文学研究部']
            wrapped = wrap(text, 22, p)
            self.assertEqual(''.join(text.split()), ''.join(wrapped.split()))
            self.assertFalse(quality(wrapped, p))
            self.assertTrue(all(width(line) <= 22 for line in wrapped.splitlines()))

    def test_oversized_phrase_and_token_not_mangled(self):
        for phrase in ['a_super_long_identifier', 'A very long official name']:
            result = wrap('前 ' + phrase + ' 后续', 10, [phrase])
            self.assertIn(phrase, result)
            self.assertTrue(any(width(line) > 10 for line in result.splitlines()))

    def test_new_diagnostics_detect_old_layout(self):
        cue = dict(id='c', start_ms=0, end_ms=3000, target='名称 The\nBand', source='name', keep_together=['The Band'])
        self.assertIn('split_phrase', [r['code'] for r in screen([cue], resolve_profile({}))])
        cue['target'] = '一些字\n器'
        self.assertIn('orphan_line', [r['code'] for r in screen([cue], resolve_profile({}))])

    def test_unreviewed_proposal_cannot_change_output(self):
        ref = reference([word('Wait',0,.3),word('Next',1.2,2)], [1])
        doc = units_for(ref,['等一下','下一句'])
        before = copy.deepcopy(doc)
        intervals = [dict(unit_id='u0',start_ms=300,end_ms=1100,reason='bounded inspected pause',evidence='local sample and measurements')]
        proposal = propose(doc,ref,intervals)['proposals'][0]['proposal']
        self.assertFalse(proposal['reviewed'])
        self.assertEqual(doc,before)
        doc['segments'][0]['tail_evidence'] = proposal
        with self.assertRaises(ValueError): compose(doc,ref)
        proposal['reviewed'] = True
        c = compose(doc,ref)['segments'][0]
        self.assertEqual((c['start_ms'],c['speech_end_ms'],c['end_ms']),(0,300,800))
        doc['segments'][0]['target'] = '新含义'
        with self.assertRaisesRegex(ValueError,'stale'): compose(doc,ref)

    def test_no_evidence_no_tail_and_cut_or_onset_bound(self):
        ref = reference([word('Wait',0,.3),word('Next',.6,1)], [1])
        doc = units_for(ref,['等一下','下一句'])
        self.assertIsNone(propose(doc,ref,[])['proposals'][0]['proposal'])
        interval = dict(unit_id='u0',start_ms=300,end_ms=1000,reason='bounded',evidence='measurement')
        p = propose(doc,ref,[interval])['proposals'][0]
        self.assertEqual(p['proposal']['display_end_ms'],520)
        self.assertGreater(p['residual_shortfall_ms'],0)
        interval['end_ms'] = 450
        self.assertEqual(propose(doc,ref,[interval])['proposals'][0]['proposal']['display_end_ms'],450)

    def test_phrase_metadata_survives_compose(self):
        ref=reference([word('Hello',0,2)])
        doc=units_for(ref,['我们选择 The Northern Lights 作为名称'])
        doc['segments'][0]['keep_together']=['The Northern Lights']
        made=compose(doc,ref,{'max_width':24})
        self.assertEqual(made['segments'][0]['keep_together'],['The Northern Lights'])
        self.assertEqual(compose(made,ref,{'max_width':24})['segments'],made['segments'])
        self.assertFalse(quality(made['segments'][0]['target'],['The Northern Lights']))

    def test_null_optional_phrases_equivalent_to_missing(self):
        ref=reference([word('Hello',0,2)])
        doc=units_for(ref,['我们选择完整的名称'])
        expected=compose(doc,ref)['segments']
        doc['segments'][0]['keep_together']=None
        self.assertEqual(compose(doc,ref)['segments'],expected)


if __name__=='__main__': unittest.main()
