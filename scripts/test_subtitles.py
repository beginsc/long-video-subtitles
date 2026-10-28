import copy
from pathlib import Path
import tempfile
import unittest

import subtitles as s
import asr_workflow as a


def document():
    return {"schema_version": 1, "timing_mode": "fixed", "segments": [
        {"id": "10", "start_ms": 1000, "end_ms": 3000, "source": "Not yet.", "target": "还没有。"},
        {"id": "20", "start_ms": 3000, "end_ms": 5000, "source": "Wait for it.", "target": "再等等。"}]}


def ranged_document():
    original = {"schema_version": 1, "segments": [
        {"id": "phrase", "start_ms": 0, "end_ms": 3000,
         "source": "A B C D E F", "asr_words": [{"word": w} for w in "ABCDEF"]}]}
    current = {"schema_version": 1, "timing_mode": "rebuild", "segments": []}
    for i, text in enumerate(["A B", "C D", "E F"]):
        current["segments"].append({"id": f"child-{i}", "start_ms": i*1000, "end_ms": (i+1)*1000,
            "source": text, "target": text, "parent_ids": ["phrase"], "source_word_ranges": [
                {"parent_id": "phrase", "start_word_index": i*2, "end_word_index_exclusive": (i+1)*2}]})
    return original, current


class SubtitleBehaviorTests(unittest.TestCase):
    def test_word_ranges_catch_lost_child_even_when_parent_is_still_present(self):
        original, current = ranged_document()
        report = s.check(current, original)
        self.assertTrue(report["ok"])
        self.assertEqual(report["coverage"]["words_checked"], 6)
        del current["segments"][1]
        self.assertFalse(s.check(current, original)["ok"])

    def test_word_ranges_reject_duplicate_invalid_or_missing_spans(self):
        for fault in ("overlap", "out_of_bounds", "boolean", "missing", "wrong_parent"):
            original, current = ranged_document()
            cue = current["segments"][1]
            span = cue["source_word_ranges"][0]
            if fault == "overlap": span["start_word_index"] = 1
            elif fault == "out_of_bounds": span["end_word_index_exclusive"] = 7
            elif fault == "boolean": span["start_word_index"] = True
            elif fault == "missing": del cue["source_word_ranges"]
            else: span["parent_id"] = "unrelated"
            with self.subTest(fault=fault): self.assertFalse(s.check(current, original)["ok"])

    def test_word_ranges_allow_cross_parent_merge_and_partial_reference_evidence(self):
        original, current = ranged_document()
        original["segments"].append({"id": "sentence", "start_ms": 3000, "end_ms": 4000,
                                     "source": "No word alignment"})
        current["segments"][-1]["end_ms"] = 4000
        current["segments"][-1]["parent_ids"].append("sentence")
        report = s.check(current, original)
        self.assertTrue(report["ok"])
        self.assertEqual(report["coverage"]["level"], "mixed")
        self.assertFalse(report["meaning_checked"])

    def test_legacy_parent_only_coverage_does_not_claim_word_coverage(self):
        original, current = ranged_document()
        for cue in current["segments"]: del cue["source_word_ranges"]
        report = s.check(current, original)
        self.assertTrue(report["ok"])
        self.assertEqual(report["coverage"]["level"], "parent_ids")
        self.assertEqual(report["coverage"]["words_checked"], 0)
        self.assertTrue(report["warnings"])

    def test_rebuilt_vtt_preserves_cue_ids_while_srt_map_tracks_renumbering(self):
        doc = document(); doc["timing_mode"] = "rebuild"
        doc["segments"][0]["id"] = "scene-1a"; doc["segments"][1]["id"] = "scene-2b"
        with tempfile.TemporaryDirectory() as tmp:
            for fmt in ("srt", "vtt"):
                path = Path(tmp)/("export."+fmt)
                s.write(path, s.export(doc, fmt))
                mapping = s.index_map(doc, path)["mapping"]
                self.assertEqual([r["cue_id"] for r in mapping], ["scene-1a", "scene-2b"])
                expected = ["1", "2"] if fmt == "srt" else ["scene-1a", "scene-2b"]
                self.assertEqual([r["exported_id"] for r in mapping], expected)
                wrong = copy.deepcopy(doc); wrong["segments"][0]["target"] = "Different meaning"
                with self.assertRaises(ValueError): s.index_map(wrong, path)

    def test_fixed_rejects_changed_time(self):
        original = document(); changed = copy.deepcopy(original)
        changed["segments"][1]["start_ms"] += 10
        self.assertFalse(s.check(changed, original, "fixed")["ok"])

    def test_fixed_preserves_nonsequential_numeric_indices(self):
        rendered = s.export(document(), "srt")
        self.assertTrue(rendered.startswith("10\n"))
        self.assertIn("\n20\n", rendered)

    def test_assembly_rejects_missing_translation_and_coverage(self):
        original = document(); incomplete = copy.deepcopy(original)
        incomplete["segments"] = incomplete["segments"][:1]
        with self.assertRaises(ValueError): s.assemble([incomplete], original, "fixed")
        untranslated = copy.deepcopy(original); untranslated["segments"][0]["target"] = ""
        with self.assertRaises(ValueError): s.assemble([untranslated], original, "fixed")

    def test_assembly_rejects_duplicate_and_out_of_order(self):
        original = document()
        with self.assertRaises(ValueError): s.assemble([original, original], original, "fixed")
        wrong = copy.deepcopy(original); wrong["segments"].reverse()
        with self.assertRaises(ValueError): s.rows(wrong)

    def test_rebuild_split_tracks_parent_coverage(self):
        original = document(); split = copy.deepcopy(original)
        split["timing_mode"] = "rebuild"
        first = split["segments"][0]
        first.update(id="10a", parent_ids=["10"], end_ms=1800)
        second = dict(first, id="10b", start_ms=1800, end_ms=3000)
        split["segments"].insert(1, second)
        report = s.check(split, original, "rebuild")
        self.assertTrue(report["ok"])
        self.assertFalse(report["meaning_checked"])
        split["segments"][0]["parent_ids"] = ["alien"]
        self.assertFalse(s.check(split, original, "rebuild")["ok"])

    def test_nested_overlaps_are_reported_without_deletion(self):
        doc = document(); doc["segments"][0]["end_ms"] = 10000
        doc["segments"].append(dict(doc["segments"][1], id="30", start_ms=6000, end_ms=7000))
        report = s.check(doc)
        self.assertTrue(report["ok"])
        self.assertIn("overlap: 10 / 30", report["warnings"])
        self.assertEqual(len(doc["segments"]), 3)

    def test_short_flash_is_warning_not_automatic_text_rewrite(self):
        doc = document(); doc["segments"][0]["end_ms"] = 1160
        report = s.check(doc)
        self.assertIn("10: duration 160 ms", report["warnings"])
        self.assertEqual(doc["segments"][0]["target"], "还没有。")

    def test_srt_and_vtt_roundtrip_matches_saved_text_and_time(self):
        doc = document()
        with tempfile.TemporaryDirectory() as tmp:
            for fmt in ("srt", "vtt"):
                path = Path(tmp) / ("字幕." + fmt)
                s.write(path, s.export(doc, fmt, bilingual=True))
                imported = s.import_file(path)
                for before, after in zip(doc["segments"], imported["segments"]):
                    self.assertEqual((before["id"], before["start_ms"], before["end_ms"]),
                                     (after["id"], after["start_ms"], after["end_ms"]))
                    self.assertEqual(after["source"], before["target"] + "\n" + before["source"])

    def test_cannot_overwrite_saved_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "work.json"
            s.write(path, document())
            with self.assertRaises(FileExistsError): s.write(path, {})
            self.assertEqual(s.load(path), document())

    def test_bad_timing_and_blank_cue_lines_fail(self):
        for field, value in (("start_ms", -1), ("end_ms", 1000), ("end_ms", True), ("source", "a\n\nb")):
            doc = document(); doc["segments"][0][field] = value
            with self.assertRaises(ValueError): s.rows(doc)

    def test_style_bearing_vtt_is_not_silently_flattened(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "styled.vtt"
            path.write_text("WEBVTT\n\n00:01.000 --> 00:02.000 position:50%\nHello\n", encoding="utf-8")
            with self.assertRaises(ValueError): s.import_file(path)

    def test_ass_rounding_and_control_syntax(self):
        self.assertEqual(s.stamp(59999, "ass"), "0:01:00.00")
        output = s.export(document(), "ass")
        self.assertIn("还没有。", output)
        doc = document(); doc["segments"][0]["target"] = "{\\pos(0,0)}Hi"
        with self.assertRaises(ValueError): s.export(doc, "ass")

    def test_hours_are_not_wrapped(self):
        self.assertEqual(s.parse_stamp("101:02:03,004"), 363723004)
        self.assertEqual(s.stamp(363723004, "srt"), "101:02:03,004")

    def test_asr_offset_applied_once_and_missing_times_stay_missing(self):
        request = {"media": {"duration_ms": 2000, "sha256": "test"}, "offset_ms": 5000,
                   "backend": "whisperx", "spec": {"model": "test"}}
        raw = {"language": "en", "segments": [{"start": .2, "end": 1.2, "text": "Not yet",
               "words": [{"word": "Not", "start": .2, "end": .4}, {"word": "yet"}]}]}
        result = a.normalize(raw, request)["segments"][0]
        self.assertEqual(result["start_ms"], 5200)
        self.assertEqual(result["asr_words"][0]["start"], .2)
        self.assertEqual(result["asr_words_offset_ms"], 5000)
        self.assertNotIn("start", result["asr_words"][1])
        self.assertEqual(result["timing_precision"], "word_partial")


if __name__ == "__main__":
    unittest.main()
