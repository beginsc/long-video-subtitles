import copy,unittest
from semantic_windows import prepare,check,digest

def fixture():
    return dict(segments=[dict(id='a',start_ms=600000,end_ms=601000,source='He will never',target='绝不可能',asr_words_offset_ms=600000,asr_words=[dict(word='He',start=0,end=.1),dict(word='will',start=.2,end=.4),dict(word='never',start=.5,end=.9)]),dict(id='b',start_ms=601200,end_ms=602000,source='hold this.',target='守住这里')])
def review(packet):
    return dict(packet_digest=digest(packet),reviewer='test reviewer',items=[dict(cue_id=w['cue_id'],decision='revise',note='Second cue reads as positive without preceding negation; review display scope.',mappings=[dict(source_cue_id='a',source_quote='never',target_cue_id='b',target_quote='守住这里')])for w in packet['windows']])

class SemanticWindows(unittest.TestCase):
    def test_cross_cue_quotes_and_absolute_word_time(self):
        d=fixture();before=copy.deepcopy(d);p=prepare(d);self.assertEqual(len(p['windows']),1)
        self.assertEqual(p['windows'][0]['context'][0]['words'][2]['start_ms'],600500)
        r=check(d,p,review(p));self.assertTrue(r['requires_followup']);self.assertEqual(r['revise'],1);self.assertEqual(d,before)
    def test_stale_source_or_packet_fails(self):
        d=fixture();p=prepare(d);r=review(p);d['segments'][1]['target']='别想守住这里'
        with self.assertRaises(ValueError):check(d,p,r)
        d=fixture();p['windows'][0]['context'][0]['source']='fabricated'
        with self.assertRaises(ValueError):check(d,p,r)
    def test_missing_duplicate_and_fabricated_quote_fail(self):
        d=fixture();p=prepare(d);r=review(p)
        for items in [[],r['items']*2,[dict(r['items'][0],mappings=[dict(source_cue_id='a',source_quote='not there',target_cue_id='b',target_quote='守住')])]]:
            with self.assertRaises(ValueError):check(d,p,dict(r,items=items))
    def test_open_evidence_survives_pass_record(self):
        d=fixture();d['segments'][0]['source_checks']=[dict(id='x',status='open',reason='unheard negative')];p=prepare(d);r=review(p);r['items'][0]['decision']='pass'
        result=check(d,p,r);self.assertEqual(result['open_source_cues'],['a']);self.assertTrue(result['requires_followup'])
    def test_repeated_words_preserve_indices_and_missing_time_not_invented(self):
        d=fixture();s=d['segments'][0];s['asr_words']=[dict(word='no',start=.1,end=.2),dict(word='no',start=.3,end=.4),dict(word='wait')]
        c=prepare(d)['windows'][0]['context'][0];self.assertEqual([w['word_index']for w in c['words']],[0,1]);self.assertEqual(c['alignment_caveats'][0]['word_index'],2)
        s['asr_words'][1]['start']=0
        with self.assertRaises(ValueError):prepare(d)
    def test_all_cues_adds_nonlexical_cases(self):
        self.assertEqual(len(prepare(fixture(),all_cues=True)['windows']),2)
    def test_short_stance_reactions_are_selected_without_mutation(self):
        for phrase in ["I am surprised.", "I really like that response.", "Something to keep in mind.", "There is no denying the risk."]:
            d=dict(segments=[dict(id='r',start_ms=0,end_ms=2500,source=phrase,target='测试译文')])
            before=copy.deepcopy(d);packet=prepare(d)
            self.assertEqual(len(packet['windows']),1)
            self.assertIn('stance_reaction',packet['windows'][0]['reasons'])
            self.assertEqual(d,before)

    def test_unique_parent_is_not_itself_a_split(self):
        d=fixture();d['segments'][1]['parent_ids']=['raw-b']
        self.assertEqual(len(prepare(d)['windows']),1)
        d['segments'][0]['parent_ids']=['raw-b']
        self.assertEqual(len(prepare(d)['windows']),2)

if __name__=='__main__':unittest.main()
