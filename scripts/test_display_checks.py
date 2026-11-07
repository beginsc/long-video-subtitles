import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import subtitles as s
from display_checks import display_width


def doc(text="等一下", end=3000):
    return {"schema_version": 1, "timing_mode": "fixed", "display_profile": {"preset": "zh-game"},
            "segments": [{"id": "10", "start_ms": 1000, "end_ms": end,
                          "source": "Wait a moment.", "target": text}]}


def accept(report):
    return [{"risk_id": r["risk_id"], "reason": "User explicitly requires this original fixed axis",
             "evidence": "Original subtitle timing retained; dynamic readability not verified"}
            for r in report["display_risks"]]


class DisplayChecksTests(unittest.TestCase):
    def test_flash_blocks_final_but_draft_does_not_rewrite(self):
        d = doc("好", 1400); before = copy.deepcopy(d)
        self.assertTrue(s.check(d)["ok"])
        self.assertEqual(s.check(d)["display_risks"][0]["code"], "short_flash")
        with self.assertRaises(ValueError): s.export(d, "srt", reference=copy.deepcopy(d), status="final")
        self.assertIn("好", s.export(d, "srt", status="draft"))
        self.assertEqual(before, d)

    def test_width_not_hidden_by_low_cps_and_mixed_width_is_measured(self):
        d = doc("字"*28, 7000)
        report = s.check(d)
        self.assertEqual([x["code"] for x in report["display_risks"]], ["line_width"])
        self.assertEqual(display_width("中文ABC e\u0301"), 9)
        with self.assertRaises(ValueError): s.export(d, "srt", reference=copy.deepcopy(d), status="final")

    def test_single_line_default_and_explicit_general_two_lines(self):
        d = doc("等一下\n先别走")
        self.assertIn("line_count", [r["code"] for r in s.check(d)["display_risks"]])
        d["display_profile"] = {"preset": "general"}
        self.assertIn("line_count", [r["code"] for r in s.check(d)["display_risks"]])
        with self.assertRaises(ValueError): s.export(d, "ass", status="draft")
        d["display_profile"]["max_lines"] = 2
        self.assertTrue(s.check(d, reference=copy.deepcopy(d), status="final")["ok"])
        d.pop("display_profile"); d["domain"] = "sc2"
        self.assertEqual(s.check(d)["display_profile"]["max_lines"], 1)

    def test_exception_is_local_and_cannot_survive_source_target_or_time_change(self):
        d = doc("好", 1400)
        d["display_exceptions"] = accept(s.check(d))
        self.assertTrue(s.check(d, reference=copy.deepcopy(d), status="final")["ok"])
        second = {"id": "20", "start_ms": 2000, "end_ms": 4000, "source": "Next", "target": "接着"}
        d["segments"].append(second)
        self.assertTrue(s.check(d, reference=copy.deepcopy(d), status="final")["ok"])
        for key,value in (("source","No"),("target","啊"),("end_ms",1350)):
            changed = copy.deepcopy(d); changed["segments"][0][key] = value
            report = s.check(changed, reference=copy.deepcopy(changed), status="final")
            self.assertFalse(report["ok"]); self.assertTrue(report["stale_exceptions"])

    def test_final_requires_reference_and_cannot_waive_axis_change(self):
        d = doc()
        self.assertFalse(s.check(d, status="final")["ok"])
        changed = copy.deepcopy(d); changed["segments"][0]["end_ms"] = 3100
        self.assertFalse(s.check(changed, reference=copy.deepcopy(d), status="final")["structural_ok"])

    def test_unresolved_issue_blocks_final_and_never_claims_meaning_checked(self):
        d = doc(); d["issues"] = [{"cue_ids":["10"],"status":"provisional","detail":"Cannot resolve speaker attribution"}]
        self.assertFalse(s.check(d)["ok"])
        self.assertTrue(s.check(d)["source_blockers"])
        self.assertFalse(s.check(d, reference=copy.deepcopy(d), status="final")["ok"])
        self.assertFalse(s.check(d)["meaning_checked"])

    def test_bilingual_checks_source_track_separately(self):
        d = doc(); d["segments"][0]["source"] = "word "*30
        self.assertTrue(s.check(d, reference=copy.deepcopy(d), status="final")["ok"])
        report = s.check(d, reference=copy.deepcopy(d), status="final", bilingual=True)
        self.assertFalse(report["ok"])
        self.assertTrue(any(x["track"] == "source" and x["code"] == "line_width" for x in report["display_risks"]))

    def test_bad_configuration_and_exception_are_rejected(self):
        for profile in ({"target_cps":float('nan')},{"max_width":True},{"max_lines":0},
                        {"flash_ms":900},{"target_cps":float('inf')},{"typo":42}):
            with self.subTest(profile=profile), self.assertRaises(ValueError): s.check(doc(), profile=profile)
        with self.assertRaises(ValueError): s.check(doc(), exceptions=[{"risk_id":"x","reason":"","evidence":"x"}])
        with self.assertRaises(ValueError): s.check(doc(), status="final", require_target=False)

    def test_assemble_preserves_profile_and_open_issues_and_rejects_conflicts(self):
        d = doc(); d["issues"] = [{"id":"display-1","kind":"display","cue_ids":["10"],"status":"open","detail":"Dynamic display not checked"}]
        result = s.assemble([d], d, "fixed")
        self.assertEqual(result["issues"], d["issues"])
        self.assertEqual(result["display_profile"], d["display_profile"])
        self.assertFalse(s.check(result, reference=copy.deepcopy(d), status="final")["ok"])
        conflicting = copy.deepcopy(d); conflicting["display_profile"] = {"preset":"general"}
        with self.assertRaises(ValueError): s.assemble([conflicting], d, "fixed")

    def test_saved_export_readback_detects_later_text_change(self):
        d = doc()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'final.srt'
            s.write(p,s.export(d,"srt",reference=copy.deepcopy(d),status="final"))
            self.assertTrue(s.index_map(d,p)["text_and_times_match"])
            d["segments"][0]["target"] = "别等了"
            with self.assertRaises(ValueError): s.index_map(d,p)

    def test_self_reference_is_rejected_even_without_other_risks(self):
        d=doc()
        self.assertFalse(s.check(d,reference=d,status="final")["ok"])
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'current.json'; out=Path(tmp)/'final.srt'
            p.write_text(json.dumps(d),encoding='utf-8')
            run=subprocess.run([sys.executable,'-B',str(Path(s.__file__)),'export',str(p),
                                '--reference',str(p),'--status','final','--output',str(out)],capture_output=True,text=True)
            self.assertNotEqual(run.returncode,0); self.assertFalse(out.exists())

    def test_whitespace_only_lines_cannot_be_exported_even_as_draft(self):
        for blank in (' ', '\u3000', ''):
            with self.subTest(blank=repr(blank)), self.assertRaises(ValueError):
                s.export(doc('守住高地\n'+blank+'\n等待援军'),'srt',status='draft')

    def test_unused_source_track_setting_does_not_invalidate_monolingual_exception(self):
        d=doc('好',1400); d['display_exceptions']=accept(s.check(d))
        self.assertTrue(s.check(d,reference=copy.deepcopy(d),status='final',profile={'source_cps':21})['ok'])
        changed=s.check(d,reference=copy.deepcopy(d),status='final',profile={'flash_ms':450})
        self.assertFalse(changed['ok']); self.assertTrue(changed['stale_exceptions'])

    def test_cli_final_rejection_leaves_no_output_and_requires_explicit_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); inp=root/'input.json'; out=root/'final.srt'; original=root/'original.json'
            inp.write_text(json.dumps(doc("好",1400)),encoding='utf-8')
            original.write_text(inp.read_text(encoding='utf-8'),encoding='utf-8')
            command=[sys.executable,'-B',str(Path(s.__file__)),'export',str(inp),'--output',str(out)]
            for extra in ([],['--status','final','--reference',str(original)],['--status','final','--reference',str(inp)]):
                run=subprocess.run(command+extra,capture_output=True,text=True)
                self.assertNotEqual(run.returncode,0); self.assertFalse(out.exists())
            run=subprocess.run(command+['--status','draft'],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr); self.assertTrue(out.exists())


if __name__ == '__main__':
    unittest.main()
