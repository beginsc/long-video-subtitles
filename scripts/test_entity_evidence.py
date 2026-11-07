import copy
import unittest
from source_guard import audit, content_fingerprint
from subtitles import export
from test_source_guard import sample, sign


def entity_doc():
    d=sample();u=d['segments'][0];u.update(source='Nora placed the parcel. Nola then sealed it.',target='诺拉放下包裹 随后封好')
    q=d['issues'][0];q['source_constraint'].update(unknown='Near-name surface form',safe_content='The continuously described actor placed then sealed a parcel',risk='referent')
    q['status']='resolved';q['disposition'].update(action='verified_repair',rationale='Explicit continuous actor reference, not a global near-name replacement',evidence='Current source span is quoted in entity_evidence',entity_evidence={
        'resolution_scope':'referent_only',
        'evidence':[{'id':'ctx','kind':'source_context','locator':'fixture:p1 words0–10','observation':'Named Nora remains the explicitly continuous actor; fixture has no competing entity'}],
        'claims':[{'slot':'actor','state':'supported','value':'Nora','evidence_ids':['ctx'],'source_form_confirmed':False}],
        'preserved_facts':['placing and sealing are both supplied actions'], 'conflicts':[], 'limitations':'No acoustic confirmation; not a source-word repair'})
    sign(d);return d


class EntityEvidenceTests(unittest.TestCase):
    def test_contextual_reference_keeps_raw_source(self):
        d=entity_doc();before=d['segments'][0]['source'];self.assertEqual(audit(d),[])
        self.assertIn('诺拉',export(d,'srt'));self.assertEqual(d['segments'][0]['source'],before)

    def test_wordlist_or_asr_agreement_alone_cannot_resolve(self):
        for kind in ['name_glossary','asr_hypothesis','external_fact']:
            d=entity_doc();d['issues'][0]['disposition']['entity_evidence']['evidence'][0]['kind']=kind;sign(d)
            self.assertIn('localized source anchor',' '.join(audit(d)))

    def test_unsupported_slot_stays_partial_and_draft(self):
        d=entity_doc();q=d['issues'][0];e=q['disposition']['entity_evidence']
        e['claims'].append(dict(slot='recipient',state='unknown',value='',evidence_ids=[],source_form_confirmed=False));sign(d)
        self.assertIn('unresolved entity slots',' '.join(audit(d)))
        q['status']='provisional';q['disposition']['action']='safe_partial';sign(d)
        self.assertEqual(audit(d),[])
        with self.assertRaises(ValueError):export(d,'srt',reference=copy.deepcopy(d),status='final')

    def test_context_does_not_certify_what_was_heard(self):
        d=entity_doc();e=d['issues'][0]['disposition']['entity_evidence'];e['claims'][0].update(source_form_confirmed=True,lexical_evidence_ids=['ctx']);sign(d)
        self.assertIn('actual audio observation',' '.join(audit(d)))
        e['claims'][0]['source_form_confirmed']=False;e['claims'][0]['repairs_source_word']=True;e['resolution_scope']='source_word';sign(d)
        self.assertIn('referent-only',' '.join(audit(d)))

    def test_actual_lexical_evidence_declaration_remains_reviewed(self):
        d=entity_doc();e=d['issues'][0]['disposition']['entity_evidence'];e['evidence'][0]['kind']='author_transcript';e['claims'][0].update(source_form_confirmed=True,lexical_evidence_ids=['ctx'],repairs_source_word=True);e['resolution_scope']='source_word';sign(d)
        self.assertEqual(audit(d),[])  # Structural acceptance does not certify the supplied transcript.
        d['issues'][0]['disposition']['reviewer']=d['issues'][0]['disposition']['translator'];sign(d)
        self.assertIn('independent',' '.join(audit(d)))

    def test_claim_or_observation_change_invalidates_review(self):
        for field in ['claim','observation']:
            d=entity_doc();e=d['issues'][0]['disposition']['entity_evidence']
            if field=='claim':e['claims'][0]['value']='Nola'
            else:e['evidence'][0]['observation']='Different speaker changes the actor'
            self.assertIn('stale',' '.join(audit(d)))

    def test_conflicting_anchor_not_verified(self):
        d=entity_doc();d['issues'][0]['disposition']['entity_evidence']['conflicts']=['Two speakers identify different people'];sign(d)
        self.assertIn('conflicts',' '.join(audit(d)))

    def test_mixed_polarity_cannot_hide_under_entity_risk(self):
        for risk,dims in [('entity_and_polarity',[]),('referent',['entity','polarity']),('polarityUncertain',[])]:
            d=entity_doc();q=d['issues'][0];q['status']='provisional';q['disposition']['action']='safe_partial';q['source_constraint'].update(risk=risk,unresolved_dimensions=dims);sign(d)
            self.assertIn('polarity',' '.join(audit(d)))

    def test_resolving_name_does_not_resolve_missing_action(self):
        d=entity_doc();d['issues'][0]['source_constraint']['unresolved_dimensions']=['predicate'];sign(d)
        self.assertIn('unresolved dimensions',' '.join(audit(d)))

    def test_missing_or_unknown_evidence_does_not_pass(self):
        d=entity_doc();d['issues'][0]['disposition']['entity_evidence']=None;sign(d)
        self.assertIn('entity_evidence requires',' '.join(audit(d)))
        d=entity_doc();e=d['issues'][0]['disposition']['entity_evidence'];e['claims'][0]['evidence_ids']=['missing'];sign(d)
        self.assertIn('unknown evidence',' '.join(audit(d)))
        d=entity_doc();d['issues'][0]['disposition']['entity_evidence']['evidence'][0]['locator']='';sign(d)
        self.assertIn('locator',' '.join(audit(d)))

    def test_new_entity_record_cannot_take_legacy_resolved_shortcut(self):
        d=entity_doc();del d['issues'][0]['source_constraint']
        self.assertIn('full independent review',' '.join(audit(d)))

    def test_only_repaired_word_slot_needs_lexical_confirmation(self):
        d=entity_doc();e=d['issues'][0]['disposition']['entity_evidence'];e['resolution_scope']='source_word'
        e['evidence'].append(dict(id='author',kind='author_transcript',locator='fixture-author-line1',observation='The author explicitly names Nora'))
        e['claims'][0].update(evidence_ids=['ctx','author'],source_form_confirmed=True,lexical_evidence_ids=['author'],repairs_source_word=True)
        e['claims'].append(dict(slot='recipient',state='supported',value='the previously identified recipient',evidence_ids=['ctx'],source_form_confirmed=False))
        sign(d);self.assertEqual(audit(d),[])


if __name__=='__main__':unittest.main()
