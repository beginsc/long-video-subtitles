import unittest,copy
from evidence_windows import plan

def doc(rows,duration=20000):return {'meta':{'duration_ms':duration},'segments':rows}
def row(sid,start,end,words=None,offset=0):
    d={'id':sid,'start_ms':start,'end_ms':end}
    if words is not None:d.update(asr_words=words,asr_words_offset_ms=offset)
    return d

class EvidenceWindowsTests(unittest.TestCase):
    def test_union_overlaps_and_bookends(self):
        d=doc([row('b',4000,9000),row('a',0,5000),row('c',14000,15000)])
        g=plan(d)['gap_candidates'];self.assertEqual([(r['start_ms'],r['end_ms']) for r in g],[(9000,14000),(15000,20000)]);self.assertEqual(g[0]['left_id'],'b')
    def test_real_words_reveal_hidden_gap_and_offset(self):
        d=doc([row('merged',0,18000,[{'start':0,'end':1},{'start':10,'end':12}],5000)])
        g=plan(d)['gap_candidates'];self.assertEqual([(r['start_ms'],r['end_ms']) for r in g],[(0,5000),(6000,15000)])
    def test_missing_alignment_caveats_not_proof(self):
        d=doc([row('partial',0,8000,[{'word':'a','start':1,'end':2},{'word':'b'}]),row('fallback',12000,18000)])
        out=plan(d);self.assertEqual(len(out['alignment_caveats']),2);self.assertIn('not independent VAD',out['interval_basis'])
    def test_dense_windows_dedup_and_endpoint(self):
        out=plan(doc([]),focus=[(100,1100),(600,1300)],step_ms=500)
        self.assertEqual(out['visual_sample_times_ms'],[100,600,1100,1300]);self.assertEqual(out['visual_status'],'planned_not_extracted_or_viewed')
    def test_bad_times_refused(self):
        for bad in [doc([row('x',-1,5)]),doc([row('x',3,2)]),doc([row('x',0,20002)]),doc([],duration=float('nan'))]:
            with self.assertRaises(ValueError):plan(bad)
    def test_frame_limit_refused(self):
        with self.assertRaises(ValueError):plan(doc([]),focus=[(0,5000)],step_ms=100,max_frames=10)
    def test_nonfinite_threshold_refused(self):
        for kwargs in [{'step_ms':float('nan')},{'min_gap_ms':float('inf')},{'max_frames':True}]:
            with self.assertRaises(ValueError):plan(doc([]),**kwargs)
    def test_no_asr_means_candidate_not_missing_sentence(self):
        out=plan(doc([]));self.assertEqual(len(out['gap_candidates']),1);self.assertIn('not proven omissions',out['note'])

    def test_long_word_offset_provenance_and_no_mutation(self):
        d=doc([row('long',4000,10000,[{'word':'missing'},{'word':'wow','start':0,'end':3},{'word':'there','start':3,'end':4}],5000)])
        d['segments'][0]['parent_ids']=['raw7'];old=copy.deepcopy(d);out=plan(d)
        r=out['word_duration_candidates'][0]
        self.assertEqual((r['word_index'],r['start_ms'],r['end_ms'],r['duration_ms']),(1,5000,8000,3000));self.assertEqual(r['parent_ids'],['raw7']);self.assertEqual(d,old)
        self.assertEqual(plan(d,max_word_ms=3000)['word_duration_candidates'],[])
        self.assertIn('not a confirmed error',r['reason'])

    def test_invalid_word_threshold_and_times(self):
        for value in [0,-1,True,float('nan'),float('inf')]:
            with self.assertRaises(ValueError):plan(doc([]),max_word_ms=value)
        with self.assertRaises(ValueError):plan(doc([row('bad',0,1000,[{'word':'x','start':2,'end':1}])]))

if __name__=='__main__':unittest.main()

