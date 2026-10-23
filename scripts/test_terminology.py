"""Synthetic behavioral tests; do not claim live fact verification."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import terminology as terms
from subtitle_workbench import row

ROOT = Path(__file__).resolve().parent


def library(domain='sc2'):
    return {'schema_version': 1, 'domain': domain, 'source_language': 'en', 'target_locale': 'zh-CN',
            'version': '1', 'entries': [{'id': 'marine', 'source': 'Marine', 'aliases': ['Marines'],
            'sense': 'Synthetic game unit', 'category': 'unit', 'preferred_target': '陆战队员',
            'status': 'confirmed', 'sources': [{'kind': 'user', 'locator': 'synthetic-test',
            'checked_at': '2026-09-06', 'note': 'Synthetic fixture only.'}], 'decision_note': 'Test convention'}]}


def discovery(doc):
    return [{'id': r['id'], 'source_digest': terms.source_digest(r), 'note': 'Synthetic coverage record',
             'candidates': []} for r in doc['segments']]


class TermsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.doc = {'schema_version': 1, 'meta': {}, 'segments': [row('s1', 0, 3000, 'Marine and Marines.') ]}

    def tearDown(self):
        self.temp.cleanup()

    def put(self, name, data):
        (self.folder/name).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def get(self, name):
        return json.loads((self.folder/name).read_text(encoding='utf-8-sig'))

    def run_tool(self, script, *args, code=0):
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(ROOT/script), *args],
                                cwd=self.folder, capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)

    def bound(self, libs=None):
        return terms.bind_document(self.doc, terms.merge_libraries(libs or [library()]), discovery(self.doc), [])

    def test_literal_boundaries_width_case_and_aliases(self):
        self.assertEqual(terms.spans('SCVs scv ＳＣＶ', 'SCV'), [(5, 8), (9, 12)])
        self.assertEqual(terms.spans('Maybe May may', 'May', True), [(6, 9)])
        self.assertEqual(terms.spans('部署星灵部队', '星灵'), [(2, 4)])
        book = terms.merge_libraries([library()])
        hits = terms.matches(self.doc['segments'][0], book)
        self.assertEqual([h['surface'] for h in hits], ['Marine', 'Marines'])

    def test_domain_locale_conflict_and_explicit_override(self):
        cs = library('cs2')
        cs['entries'][0]['preferred_target'] = '海军陆战队'
        book = terms.merge_libraries([library(), cs])
        self.assertEqual(len(terms.matches(self.doc['segments'][0], book)[0]['entry_keys']), 2)
        self.doc['segments'][0]['term_domains'] = ['cs2']
        self.assertEqual(terms.matches(self.doc['segments'][0], book)[0]['entry_keys'], ['cs2:marine'])
        cs['target_locale'] = 'zh-TW'
        with self.assertRaises(ValueError):
            terms.merge_libraries([library(), cs])
        override = library()
        override['entries'][0]['preferred_target'] = '机枪兵'
        with self.assertRaises(ValueError):
            terms.merge_libraries([library(), override])
        override['entries'][0].update(replaces_digest=terms.digest(library()['entries'][0]), override_reason='Synthetic user style')
        self.assertEqual(terms.merge_libraries([library(), override])['entries'][0]['entry']['preferred_target'], '机枪兵')
        with self.assertRaises(ValueError):
            terms.merge_libraries([override])

    def test_full_video_discovery_coverage_and_unknown_candidates(self):
        self.doc['segments'] = [row(f's{i}', i*4000, i*4000+3000, 'Ordinary words.') for i in range(125)]
        self.doc['segments'][-1]['source'] = 'Newname appears only in the final chapter.'
        self.put('source.json', self.doc)
        self.put('library.json', library())
        self.run_tool('glossary_workbench.py', 'prepare', 'source.json', 'batches', '--glossary', 'library.json')
        packets = [self.get(str(p.relative_to(self.folder))) for p in sorted((self.folder/'batches').glob('*.json'))]
        self.assertEqual([r['id'] for p in packets for r in p['segments']], [r['id'] for r in self.doc['segments']])
        records = discovery(self.doc)
        with self.assertRaises(ValueError):
            terms.bind_document(self.doc, terms.merge_libraries([library()]), records[:-1], [])
        records[-1]['candidates'] = [{'surface': 'Newname', 'decision': 'add', 'entry_key': 'sc2:newname', 'note': 'Unknown name'}]
        with self.assertRaises(ValueError):
            terms.bind_document(self.doc, terms.merge_libraries([library()]), records, [])
        lib = library()
        new = copy.deepcopy(lib['entries'][0])
        new.update(id='newname', source='Newname', aliases=[], status='proposed', sources=[], preferred_target='')
        lib['entries'].append(new)
        bound = terms.bind_document(self.doc, terms.merge_libraries([lib]), records, [])
        self.assertTrue(any(f['id'] == 's124' and f['code'] == 'term_unconfirmed' for f in terms.findings(bound)))

    def test_occurrence_senses_overlaps_and_stale_resolutions(self):
        lib = library()
        lib['entries'][0].update(source='VS', aliases=[], requires_context=True)
        other = copy.deepcopy(lib['entries'][0])
        other.update(id='starport', sense='Another building', preferred_target='星港')
        lib['entries'].append(other)
        self.doc['segments'][0]['source'] = 'VS then VS.'
        book = terms.merge_libraries([lib])
        initial = terms.bind_document(self.doc, book, discovery(self.doc), [])
        self.assertEqual(sum(f['code'] == 'term_ambiguous' for f in terms.findings(initial)), 2)
        records = [{'id': 's1', 'source_digest': terms.source_digest(self.doc['segments'][0]), 'decisions': [
            {'start': 0, 'end': 2, 'action': 'use', 'entry_key': 'sc2:starport', 'note': 'Synthetic building context'},
            {'start': 8, 'end': 10, 'action': 'not_term', 'note': 'Versus, not a building'}]}]
        bound = terms.bind_document(self.doc, book, discovery(self.doc), records)
        bound['segments'][0]['target'] = '星港，对阵。'
        self.assertEqual(terms.findings(bound), [])
        self.doc['segments'][0]['source_notes'] = ['Changed audio evidence']
        with self.assertRaises(ValueError):
            terms.bind_document(self.doc, book, discovery(self.doc), records)
        lib = library()
        lib['entries'][0]['aliases'] = ['Marine unit']
        self.doc['segments'][0]['source'] = 'Marine unit'
        self.assertEqual(len(terms.matches(self.doc['segments'][0], terms.merge_libraries([lib]))), 1)

    def test_snapshot_input_edits_and_rebinding_invalidate_approvals(self):
        bound = self.bound()
        r = bound['segments'][0]
        r.update(draft='陆战队员。', target='陆战队员。', review_status='reviewed', editing_digest='old')
        self.assertEqual(terms.findings(bound), [])
        changed = copy.deepcopy(bound)
        changed['terminology']['entries'][0]['entry']['preferred_target'] = '机枪兵'
        self.assertEqual(terms.findings(changed)[0]['code'], 'terminology_stale')
        changed = copy.deepcopy(bound)
        changed['segments'][0]['source'] = 'Marine retreats.'
        self.assertEqual(terms.findings(changed)[0]['code'], 'terminology_stale')
        fresh = terms.bind_document(bound, bound['terminology'], discovery(bound), [])
        self.assertEqual(fresh['segments'][0]['draft'], '陆战队员。')
        self.assertEqual(fresh['segments'][0]['target'], '')
        self.assertNotIn('editing_digest', fresh['segments'][0])

    def test_evidence_and_target_conflicts_rejected(self):
        for mutate in (lambda e: e.update(sources=[]), lambda e: e.update(preferred_target=''),
                       lambda e: e.update(forbidden_targets=['陆战队员'])):
            lib = library()
            mutate(lib['entries'][0])
            with self.assertRaises(ValueError):
                terms.validate_library(lib)

    def test_translation_proofreading_editor_export_integration(self):
        self.doc['meta']['workflow'] = 'vidove-inspired-v1'
        self.doc['segments'][0]['vision_status'] = 'skipped'
        self.put('baseline.json', self.doc)
        bound = self.bound()
        self.put('bound.json', bound)
        current = 'bound.json'
        stages = [('draft', {'id': 's1', 'draft': '陆战队员和陆战队员。'}),
                  ('proofread', {'id': 's1', 'proofreading_status': 'PASS', 'suggestions': [], 'proofreading_note': 'Synthetic term check'}),
                  ('edit', {'id': 's1', 'target': '陆战队员和陆战队员。', 'review_status': 'reviewed', 'review_note': 'Synthetic editor check', 'issues': []})]
        for stage, change in stages:
            batches = stage + '-batches'
            self.run_tool('subtitle_workbench.py', 'chunks', current, batches)
            packet = self.get(batches+'/b00001.json')
            self.assertEqual(packet['segments'][0]['term_context']['entries'][0]['key'], 'sc2:marine')
            response = {'batch_id': packet['batch_id'], 'input_digest': packet['input_digest'], 'segments': [change]}
            self.put('result-'+stage+'.json', response)
            if stage == 'edit':
                bad = copy.deepcopy(response)
                bad['segments'][0]['target'] = '海军陆战队。'
                self.put('bad.json', bad)
                self.run_tool('subtitle_workbench.py', 'apply', current, batches+'/b00001.json', 'bad.json', 'blocked.json', '--stage', stage, code=1)
                self.assertFalse((self.folder/'blocked.json').exists())
            output = stage+'.json'
            self.run_tool('subtitle_workbench.py', 'apply', current, batches+'/b00001.json', 'result-'+stage+'.json', output, '--stage', stage)
            current = output
        self.run_tool('subtitle_workbench.py', 'export', current, 'exports', '--baseline', 'baseline.json')
        self.assertTrue((self.folder/'exports/glossary.snapshot.json').exists())
        self.run_tool('glossary_workbench.py', 'export', current, 'glossary.csv')
        self.assertIn('陆战队员', (self.folder/'glossary.csv').read_text(encoding='utf-8-sig'))
        edited = self.get(current)
        edited['segments'][0]['target'] = '机枪兵。'
        self.put('drift.json', edited)
        self.run_tool('subtitle_workbench.py', 'qa', 'drift.json', 'qa.json', code=2)
        self.assertIn('term_missing_target', {x['code'] for x in self.get('qa.json')['findings']})

    def test_literal_exception_is_scoped_and_does_not_hide_unconfirmed(self):
        bound = self.bound()
        r = bound['segments'][0]
        r['target'] = '他们。'
        r['term_exceptions'] = [{'entry_key': 'sc2:marine', 'reason': 'Synthetic pronoun example',
            'input_digest': terms.digest({'source_digest': terms.source_digest(r), 'target': r['target'], 'term_context': r['term_context']})}]
        self.assertEqual(terms.findings(bound), [])
        r['target'] = '它。'
        self.assertIn('term_missing_target', {f['code'] for f in terms.findings(bound)})
        lib = library()
        lib['entries'][0]['status'] = 'proposed'
        unresolved = self.bound([lib])
        unresolved['segments'][0]['term_exceptions'] = r['term_exceptions']
        self.assertIn('term_unconfirmed', {f['code'] for f in terms.findings(unresolved)})

    def test_starters_and_empty_new_domain(self):
        for domain in ('sc2', 'cs2', 'medicine'):
            self.run_tool('glossary_workbench.py', 'init', domain+'.json', '--domain', domain)
            result = self.get(domain+'.json')
            terms.validate_library(result)
            if domain == 'medicine':
                self.assertEqual(result['entries'], [])
            elif domain == 'sc2':
                self.assertEqual(result['profile'], 'commentary-zh-CN')
                self.assertIn('marine', {e['id'] for e in result['entries']})
            else:
                self.assertIn('smoke-grenade', {e['id'] for e in result['entries']})


if __name__ == '__main__':
    unittest.main()
