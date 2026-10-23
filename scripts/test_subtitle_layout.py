import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import subtitle_layout as l
import subtitle_workbench as w


class LayoutTest(unittest.TestCase):
    def test_protects_terms_names_and_punctuation(self):
        text='所以他不知道对面是光影议会、机械台，还是星门。'
        result=l.wrap(text,28,['光影议会','机械台','星门'])
        self.assertEqual(l.compact(text),l.compact(result))
        self.assertIn('光影议会',result)
        self.assertTrue(all(not s.startswith(('，','、','。')) for s in result.splitlines()))
        english=l.wrap('The SK Telecom T1 player sees a Command Center.',22,['SK Telecom T1','Command Center'])
        self.assertIn('SK Telecom T1',english);self.assertIn('Command Center',english)
        self.assertTrue(all(l.width(s)<=22 for s in english.splitlines()))

    def test_long_indivisible_token_stays_visible(self):
        text='INnoVationSuperLongName'
        self.assertEqual(l.wrap(text,5,[text]),text)
        self.assertGreater(l.width(l.wrap(text,5)),5)

    def test_balances_lines_instead_of_orphaning_final_number(self):
        text='These players are competing in a best of 5.'
        # At width 24 no word boundary allows two lines (17 + 25).
        # Width 26 permits two lines; narrower output must remain balanced.
        narrow=l.wrap(text,24).splitlines()
        self.assertEqual(len(narrow),3)
        self.assertTrue(all(l.width(s)>12 for s in narrow))
        lines=l.wrap(text,26).splitlines()
        self.assertEqual(len(lines),2)
        self.assertTrue(all(l.width(s)>12 for s in lines))
        self.assertEqual(l.compact('\n'.join(lines)),l.compact(text))

    def test_unicode_and_explicit_lines_preserve_content(self):
        text='测试Cafe\u0301与名字。\nSecond explicit line.'
        self.assertEqual(l.compact(l.wrap(text,12)),l.compact(text))
        self.assertNotIn('\n\u0301',l.wrap(text,12))

    def test_export_qa_share_display_and_preserve_approvals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);r=w.row('s1',0,6000,'The Command Center moves across the map to a different location.')
            r.update(target='指挥中心正在飞向地图的另一边。',review_status='reviewed',review_note='Synthetic fixture')
            r['term_context']={'entries':[{'entry':{'source':'Command Center','preferred_target':'指挥中心','aliases':[]}}]}
            doc=dict(schema_version=1,meta={},segments=[r]);original=copy.deepcopy(doc)
            args=SimpleNamespace(input=root/'input.json',output_dir=root/'export',baseline=None,duration_ms=None,
                bilingual=True,min_seconds=.8,max_seconds=7,max_width=42,max_lines=2,bilingual_lines=2,bilingual_total_lines=4,
                target_cps=12,source_cps=20,draft=True,format='srt',source_first=False,wrap=True,target_width=24,source_width=38)
            w.save(args.input,doc);w.export(args)
            self.assertEqual(doc,original);self.assertEqual(w.load(args.input),original)
            plan=w.load(root/'export/layout.json');srt=(root/'export/draft.srt').read_text(encoding='utf-8')
            self.assertIn(plan['segments'][0]['source'],srt);self.assertIn(plan['segments'][0]['target'],srt)
            codes={f['code'] for f in w.load(root/'export/qa.json')['findings']}
            self.assertNotIn('source_width',codes);self.assertNotIn('target_width',codes)
            self.assertEqual((plan['segments'][0]['start_ms'],plan['segments'][0]['end_ms']),(0,6000))
            self.assertIn('layout.json',w.load(root/'export/delivery-manifest.json')['files'])
            w.verify_export(SimpleNamespace(output_dir=root/'export'))

    def test_wrapping_cannot_hide_high_cps_or_excess_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=w.row('s1',0,1000,'This is an extremely fast and very long source sentence. '*4)
            r.update(target='很长的字幕'*10,review_status='reviewed',review_note='fixture')
            args=SimpleNamespace(baseline=None,duration_ms=None,bilingual=True,min_seconds=.8,max_seconds=7,
                max_width=42,max_lines=2,bilingual_lines=2,bilingual_total_lines=4,target_cps=12,source_cps=20,
                wrap=True,target_width=20,source_width=30)
            report=w.check(dict(schema_version=1,meta={},segments=[r]),args)
            codes={f['code'] for f in report['findings']}
            self.assertTrue({'source_cps','target_cps','bilingual_total_layout'}<=codes)


if __name__=='__main__':unittest.main()
