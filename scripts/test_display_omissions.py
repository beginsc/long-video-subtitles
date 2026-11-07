"""Synthetic functional tests; no held-out subtitles or media."""
import copy
import unittest

from display_omissions import propose, fingerprint
from source_guard import content_fingerprint, audit as audit_source
from subtitles import check, export
from unified_cues import compose


def fixture():
    ref = dict(schema_version=1, segments=[])
    units = []
    for n, (text, target, start, end) in enumerate([
            ('Explanation', '说明', 0, 2), ('um', '', 2.4, 2.6),
            ('Conclusion', '结论', 3, 5)]):
        pid, uid = 'p' + str(n), 'u' + str(n)
        ref['segments'].append(dict(id=pid, source=text, target='', start_ms=int(start * 1000),
                                    end_ms=int(end * 1000), asr_words=[dict(word=text, start=start, end=end)]))
        units.append(dict(id=uid, source=text, target=target, parent_ids=[pid],
                          start_ms=int(start * 1000), end_ms=int(end * 1000),
                          source_word_ranges=[dict(parent_id=pid, start_word_index=0, end_word_index_exclusive=1)],
                          join_next=True, join_reason='continuous thought'))
    doc = dict(schema_version=1, timing_mode='rebuild', segments=units)
    approve(units[1], ref)
    return ref, doc


def approve(unit, ref):
    unit['target'] = ''
    unit['display_omission'] = propose(unit, ref, 'nonsemantic hesitation in this context',
                                     'translator', 'synthetic source context; no media claim')
    unit['display_omission']['reviewed_by'] = 'independent reviewer'
    unit['display_omission']['reviewed_sha256'] = fingerprint(unit, ref)


class OmissionTests(unittest.TestCase):
    def test_full_provenance_two_real_cues_and_exact_coverage(self):
        ref, doc = fixture()
        saved = copy.deepcopy(doc)
        out = compose(doc, ref)
        self.assertEqual(doc, saved)
        self.assertEqual(out['units'], doc['segments'])
        self.assertEqual(out['omitted_units'], [doc['segments'][1]])
        self.assertEqual([(c['start_ms'], c['end_ms'], c['target']) for c in out['segments']],
                         [(0, 2000, '说明'), (3000, 5000, '结论')])
        result = check(out, ref, status='final')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['coverage']['words_checked'], 3)
        self.assertEqual(result['coverage']['displayed_words'], 2)
        self.assertEqual(result['coverage']['omitted_words'], 1)
        self.assertEqual(out['coverage']['words'], 3)
        self.assertFalse(result['meaning_checked'])

    def test_export_formats_never_emit_empty_cue(self):
        ref, doc = fixture()
        out = compose(doc, ref)
        for fmt, marker in [('srt', '-->'), ('vtt', '-->'), ('ass', 'Dialogue:')]:
            with self.subTest(fmt=fmt):
                rendered = export(out, fmt, reference=ref, status='final')
                self.assertEqual(rendered.count(marker), 2)
                self.assertIn('说明', rendered)
                self.assertIn('结论', rendered)
                self.assertNotIn('um', rendered)

    def test_unapproved_and_same_person_are_rejected(self):
        for mutation in [lambda o: o.pop('reviewed_sha256'), lambda o: o.pop('reviewed_by'),
                         lambda o: o.update(reviewed_by=' TRANSLATOR ')]:
            ref, doc = fixture()
            mutation(doc['segments'][1]['display_omission'])
            with self.assertRaises(ValueError):
                compose(doc, ref)

    def test_empty_target_is_never_implicit_omission(self):
        ref, doc = fixture()
        doc['segments'][1].pop('display_omission')
        with self.assertRaises(ValueError):
            compose(doc, ref)

    def test_nonempty_or_whitespace_omission_target_rejected(self):
        for target in ['嗯', ' ', '\n']:
            ref, doc = fixture()
            doc['segments'][1]['target'] = target
            with self.assertRaises(ValueError):
                compose(doc, ref)

    def test_binding_invalidated_by_source_time_reason_or_identity(self):
        mutations = [lambda u: u.update(source='No'), lambda u: u.update(start_ms=2401),
                     lambda u: u['display_omission'].update(reason='other reason'),
                     lambda u: u['display_omission'].update(evidence='other evidence'),
                     lambda u: u['display_omission'].update(reviewed_by='third reviewer')]
        for mutation in mutations:
            ref, doc = fixture()
            mutation(doc['segments'][1])
            with self.assertRaises(ValueError):
                compose(doc, ref)

    def test_changed_raw_word_and_neighbor_invalidates_binding(self):
        for idx in [0, 1, 2]:
            ref, doc = fixture()
            ref['segments'][idx]['asr_words'][0]['word'] += ' changed'
            with self.assertRaises(ValueError):
                compose(doc, ref)
        ref, doc = fixture()
        ref['segments'][1]['asr_words'][0]['start'] += .00001
        with self.assertRaises(ValueError):
            compose(doc, ref)

    def test_missing_duplicate_and_out_of_order_words_rejected(self):
        for mode in ['missing', 'duplicate', 'reordered']:
            ref, doc = fixture()
            if mode == 'missing':
                doc['segments'].pop(1)
            elif mode == 'duplicate':
                item = copy.deepcopy(doc['segments'][1])
                item['id'] = 'duplicate'
                doc['segments'].insert(1, item)
            else:
                doc['segments'].reverse()
            with self.assertRaises(ValueError):
                compose(doc, ref)

    def test_all_omitted_rejected_without_empty_export(self):
        ref, doc = fixture()
        for u in doc['segments']:
            approve(u, ref)
        with self.assertRaisesRegex(ValueError, 'All units'):
            compose(doc, ref)

    def test_hard_boundaries_and_omission_do_not_join_neighbors(self):
        ref, doc = fixture()
        doc['boundary_plan'] = dict(boundaries=[dict(before=dict(parent_id='p2', word_index=0),
                                                   kind='hard', reason='later conclusion')])
        out = compose(doc, ref)
        self.assertEqual([c['unit_ids'] for c in out['segments']], [['u0'], ['u2']])
        self.assertEqual(out['segments'][1]['start_ms'], 3000)

    def test_omission_tail_and_neighbor_borrowed_time_rejected(self):
        ref, doc = fixture()
        doc['segments'][1]['tail_evidence'] = {'reviewed': True}
        with self.assertRaises(ValueError):
            compose(doc, ref)
        ref, doc = fixture()
        out = compose(doc, ref)
        out['segments'][0]['end_ms'] = 2600
        self.assertFalse(check(out, ref)['ok'])
        out = compose(doc, ref)
        out['segments'][1]['start_ms'] = 2400
        self.assertFalse(check(out, ref)['ok'])

    def test_check_and_export_revalidate_approval(self):
        ref, doc = fixture()
        out = compose(doc, ref)
        for unit in [out['units'][1], out['omitted_units'][0]]:
            unit['display_omission']['reviewed_sha256'] = 'forged'
        self.assertFalse(check(out, ref)['ok'])
        with self.assertRaises(ValueError):
            export(out, 'srt', reference=ref, status='draft')

    def test_missing_record_and_fake_coverage_cannot_hide_gap(self):
        ref, doc = fixture()
        out = compose(doc, ref)
        out['omitted_units'] = []
        out['coverage'] = dict(words=3, exact_once=True)
        self.assertFalse(check(out, ref)['ok'])
        out.pop('units')
        self.assertFalse(check(out, ref)['ok'])

    def test_original_reference_required_even_for_draft(self):
        ref, doc = fixture()
        out = compose(doc, ref)
        self.assertFalse(check(out)['ok'])
        self.assertTrue(audit_source(out))
        self.assertEqual(audit_source(out, ref), [])

    def test_fabricated_omission_cannot_justify_deleted_translation(self):
        ref, doc = fixture()
        out = compose(doc, ref)
        # Reuse a real approval for a different range, even in both saved copies.
        fake = copy.deepcopy(out['omitted_units'][0])
        fake.update(id='u0', source='Explanation', parent_ids=['p0'], start_ms=0, end_ms=2000,
                    source_word_ranges=copy.deepcopy(out['units'][0]['source_word_ranges']))
        out['units'][0] = fake
        out['omitted_units'].insert(0, copy.deepcopy(fake))
        out['segments'].pop(0)
        out['coverage'] = dict(words=3, exact_once=True)
        self.assertFalse(check(out, ref, status='final')['ok'])
        with self.assertRaises(ValueError):
            export(out, 'srt', reference=ref, status='final')

    def test_word_overlap_between_display_and_omission_fails_export(self):
        ref, doc = fixture()
        out = compose(doc, ref)
        out['segments'][0]['source_word_ranges'].extend(copy.deepcopy(out['omitted_units'][0]['source_word_ranges']))
        out['segments'][0]['parent_ids'].append('p1')
        result = check(out, ref)
        self.assertFalse(result['ok'])
        self.assertTrue(any('overlapping' in x for x in result['failures']))
        with self.assertRaises(ValueError):
            export(out, 'srt', reference=ref, status='draft')

    def test_omitted_bookends_do_not_shift_surviving_cue(self):
        ref, doc = fixture()
        doc['segments'][1].pop('display_omission')
        doc['segments'][1]['target'] = '嗯'
        for n in [0, 2]:
            approve(doc['segments'][n], ref)
        out = compose(doc, ref)
        self.assertEqual(len(out['segments']), 1)
        self.assertEqual((out['segments'][0]['start_ms'], out['segments'][0]['end_ms']), (2400, 2600))
        self.assertTrue(check(out, ref)['ok'])

    def test_unresolved_issues_cannot_be_omitted(self):
        ref, doc = fixture()
        doc['issues'] = [dict(id='q', unit_ids=['u1'], cue_ids=['u1'], status='provisional', detail='unknown intent')]
        with self.assertRaisesRegex(ValueError, 'unresolved issue'):
            compose(doc, ref)

    def test_resolved_issue_record_survives_empty_cue_scope(self):
        ref, doc = fixture()
        doc['issues'] = [dict(id='q', unit_ids=['u1'], cue_ids=['u1'], status='resolved', detail='checked filler function')]
        out = compose(doc, ref)
        self.assertEqual(out['issues'][0]['cue_ids'], [])
        self.assertEqual(out['issues'][0]['omitted_unit_ids'], ['u1'])
        self.assertEqual(out['issues'][0]['detail'], doc['issues'][0]['detail'])
        self.assertTrue(check(out, ref, status='final')['ok'])

    def test_source_guard_binding_still_required_for_resolved_issue(self):
        ref, doc = fixture()
        issue = dict(id='q', unit_ids=['u1'], cue_ids=['u1'], status='resolved', detail='source doubt resolved',
            source_constraint=dict(unknown='initial function doubt', safe_content='nonsemantic filler',
                                   forbidden_inference=['no added speech act'], risk='intent', material=True),
            disposition=dict(action='verified_repair', translator='t', reviewer='r',
                             rationale='resolved by localized source evidence', evidence='synthetic context'))
        doc['issues'] = [issue]
        issue['disposition']['reviewed_sha256'] = content_fingerprint(doc, issue)
        out = compose(doc, ref)
        self.assertTrue(check(out, ref)['ok'])
        out['issues'][0]['disposition']['evidence'] = 'changed'
        self.assertFalse(check(out, ref)['ok'])

    def test_old_document_without_omissions_still_works(self):
        ref, doc = fixture()
        doc['segments'][1].pop('display_omission')
        doc['segments'][1]['target'] = '嗯'
        out = compose(doc, ref)
        self.assertFalse(out['omitted_units'])
        self.assertTrue(check(out, ref)['ok'])


if __name__ == '__main__':
    unittest.main()
