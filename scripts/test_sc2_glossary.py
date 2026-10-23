"""Real SC2 glossary with synthetic caster sentences; offline regression checks."""
import json
from pathlib import Path
import unittest

import terminology as terms
from subtitle_workbench import row


class SC2GlossaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parent.parent/'assets/glossaries/sc2/en-zh-CN.json'
        cls.library = json.loads(path.read_text(encoding='utf-8'))
        cls.book = terms.merge_libraries([cls.library])

    def test_spoken_names_plural_forms_and_consistent_targets(self):
        examples = [('Marines and Marauders arrive in two Medivacs.', '枪兵和光头坐着两架运输机赶到。'),
                    ('The Banshees kill the SCVs.', '女妖击杀了SCV。'),
                    ('Zerglings and Banelings surround the Thors.', '小狗和毒爆包围了雷神。'),
                    ('A Warp Prism carries two Archons.', '一架棱镜载着两个白球。')]
        doc = {'schema_version': 1, 'meta': {}, 'segments': [row(str(i),i*5000,i*5000+4000,s) for i,(s,_) in enumerate(examples)]}
        records = [{'id': r['id'], 'source_digest': terms.source_digest(r), 'note': 'Synthetic caster fixture', 'candidates': []} for r in doc['segments']]
        contextual_row = doc['segments'][2]
        start = contextual_row['source'].index('surround')
        resolutions = [{'id': '2', 'source_digest': terms.source_digest(contextual_row), 'decisions': [
            {'start': start, 'end': start+len('surround'), 'action': 'use', 'entry_key': 'sc2:surround',
             'note': 'Synthetic combat sentence: the units encircle the Thors.'}]}]
        bound = terms.bind_document(doc, self.book, records, resolutions)
        for r,(_,target) in zip(bound['segments'],examples):
            r['target'] = target
        self.assertEqual(terms.findings(bound), [])
        bound['segments'][0]['target'] = '陆战队员与劫掠者抵达。'
        self.assertIn('term_missing_target', {f['code'] for f in terms.findings(bound)})

    def test_hotkeys_are_not_cross_race_english_aliases(self):
        self.assertEqual(terms.matches({'source':'VS BB BE'}, self.book), [])
        hit = terms.matches({'source':'BCs'}, self.book)
        self.assertEqual(hit[0]['entry_keys'], ['sc2:battlecruiser'])
        self.assertEqual(terms.matches({'source':'Worker'}, self.book)[0]['entry_keys'], ['sc2:worker'])
        keys = {k for h in terms.matches({'source':'Proxy Stargate and Proxy Starport'},self.book) for k in h['entry_keys']}
        self.assertTrue({'sc2:proxy-stargate','sc2:proxy-starport'} <= keys)

    def test_new_complete_names_do_not_capture_generic_shields_or_other_maps(self):
        hits = terms.matches({'source': 'Combat Shields are ready on Golden Wall.'}, self.book)
        keys = {key for hit in hits for key in hit['entry_keys']}
        self.assertTrue({'sc2:combat-shield', 'sc2:golden-wall'} <= keys)
        other = terms.matches({'source': 'Protoss shields on Golden Pillars.'}, self.book)
        other_keys = {key for hit in other for key in hit['entry_keys']}
        self.assertNotIn('sc2:combat-shield', other_keys)
        self.assertNotIn('sc2:golden-wall', other_keys)
        # Binding the new names must expose a wrong recalled map name in QA.
        doc = {'schema_version': 1, 'meta': {}, 'segments': [row('map', 0, 4000, 'Golden Wall')]}
        rec = [{'id': 'map', 'source_digest': terms.source_digest(doc['segments'][0]),
                'candidates': [], 'note': 'Synthetic map identity already established'}]
        bound = terms.bind_document(doc, self.book, rec, [])
        bound['segments'][0]['target'] = '黄金之墙'
        self.assertTrue(terms.findings(bound))
        bound['segments'][0]['target'] = '黄金墙'
        self.assertEqual(terms.findings(bound), [])

    def test_polysemy_remains_unresolved_without_context(self):
        source = 'This is mine. He floats the Barracks while floating minerals. Charge!'
        doc = {'schema_version':1,'meta':{},'segments':[row('s1',0,6000,source)]}
        rec = [{'id':'s1','source_digest':terms.source_digest(doc['segments'][0]),'candidates':[],'note':'Ambiguous synthetic text'}]
        bound = terms.bind_document(doc,self.book,rec,[])
        self.assertIn('term_ambiguous',{f['code'] for f in terms.findings(bound)})
        by={e['id']:e for e in self.library['entries']}
        self.assertTrue(by['widow-mine']['requires_context'])
        self.assertNotEqual(by['tempest']['preferred_target'],by['psionic-storm']['preferred_target'])
        self.assertNotEqual(by['creep']['preferred_target'],by['creep-tumor']['preferred_target'])
        self.assertNotEqual(by['photon-cannon']['preferred_target'],by['spine-crawler']['preferred_target'])

    def test_history_and_corrections_remain_traceable(self):
        by={e['id']:e for e in self.library['entries']}
        for eid in ('mothership-core','battery-overcharge','mag-field-accelerator'):
            self.assertTrue(by[eid]['era'])
            self.assertTrue(by[eid]['requires_context'])
            self.assertTrue(any(s['kind']=='official' for s in by[eid]['sources']))
        for spelling in ('Corrusive Bile','Statis Ward','Mass Phonenix','Mac Field'):
            # A shorter valid alias such as Bile can still be found, but the
            # misspelled complete phrase must not become a canonical alias.
            self.assertFalse(any(h['surface'] == spelling for h in terms.matches({'source':spelling},self.book)))
        for surface,eid in [('Corrosive Bile','corrosive-bile'),('Stasis Ward','stasis-ward')]:
            self.assertIn('sc2:'+eid,{k for h in terms.matches({'source':surface},self.book) for k in h['entry_keys']})

    def test_float_buildings_and_resources_have_separate_senses(self):
        hits=terms.matches({'source':'He floats the Barracks while floating minerals.'},self.book)
        floating=[h for h in hits if h['surface'].lower() in ('floats','floating')]
        self.assertEqual(len(floating),2)
        self.assertTrue(all(set(h['entry_keys'])=={'sc2:float','sc2:float-building'} for h in floating))
        by={e['id']:e for e in self.library['entries']}
        self.assertEqual(by['float']['preferred_target'],'资源剩余')
        self.assertEqual(by['float-building']['preferred_target'],'升空')
        self.assertTrue(by['float-building']['requires_context'])


if __name__ == '__main__':
    unittest.main()
