"""Behavior tests using synthetic words only; no holdout media or ASR."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import contextlib
import io

from unified_cues import compose, prepare, main, wrap_text, canonical_hash


def reference(words, cuts=()):
    parents, begin = [], 0
    for i, end in enumerate(list(cuts) + [len(words)]):
        parents.append(dict(id="p" + str(i), asr_words=copy.deepcopy(words[begin:end]), asr_words_offset_ms=0))
        begin = end
    return dict(schema_version=1, segments=parents)


def word(text, start, end):
    return dict(word=text, start=start, end=end)


def units_for(ref, targets):
    units = []
    for i, (p, target) in enumerate(zip(ref["segments"], targets)):
        units.append(dict(id="u" + str(i), source=" ".join(w["word"] for w in p["asr_words"]), target=target,
                          source_word_ranges=[dict(parent_id=p["id"], start_word_index=0, end_word_index_exclusive=len(p["asr_words"]))]))
    return dict(schema_version=1, segments=units)


def text_sequence(cues, field):
    return "".join("".join(c[field].split()) for c in cues)


class UnifiedCueTests(unittest.TestCase):
    def setUp(self):
        self.words = [word("word" + str(i), i * .3, i * .3 + .22) for i in range(24)]

    def test_prepare_invariant_to_parent_segmentation(self):
        a = prepare(reference(self.words))
        b = prepare(reference(self.words, [1, 3, 9, 12, 17, 23]))
        summary = lambda doc: [(s["source"], s["start_ms"], s["end_ms"]) for s in doc["segments"]]
        self.assertEqual(summary(a), summary(b))
        self.assertTrue(any(len(s["parent_ids"]) > 1 for s in b["segments"]))
        self.assertEqual(text_sequence(a["segments"], "source"), "".join(w["word"] for w in self.words))

    def test_inputs_immutable_and_deterministic(self):
        ref = reference(self.words)
        before = copy.deepcopy(ref)
        a, b = prepare(ref), prepare(ref)
        self.assertEqual(a, b)
        self.assertEqual(ref, before)
        a["segments"][0]["source_word_ranges"][0]["start_word_index"] = 9
        self.assertEqual(ref, before)

    def test_hard_boundary_protects_short_answer(self):
        ref = reference([word("Question?", 0, .3), word("No!", .4, .7)])
        plan = {"boundaries": [{"before": {"parent_id": "p0", "word_index": 1}, "kind": "hard", "reason": "independent answer"}]}
        result = prepare(ref, plan)
        self.assertEqual([c["source"] for c in result["segments"]], ["Question?", "No!"])
        self.assertEqual(result["segments"][1]["start_ms"], 400)
        self.assertTrue(result["segments"][1]["break_before"])
        self.assertEqual(result["meta"]["unified_cues"]["plan_sha256"], canonical_hash(plan))

    def test_bad_plan_and_parameters_rejected(self):
        ref = reference(self.words)
        with self.assertRaises(ValueError):
            prepare(ref, {"boundaries": [{"before": {"parent_id": "unknown", "word_index": 0}, "kind": "hard", "reason": "turn"}]})
        with self.assertRaises(ValueError):
            prepare(ref, parameters={"backend": "whisperx"})

    def test_soft_plan_can_yield_to_short_duration_preference(self):
        ref = reference([word("setup", 0, .3), word("continuation", .4, .7)])
        plan = {"boundaries": [{"before": {"parent_id": "p0", "word_index": 1}, "kind": "soft", "reason": "weak phrase break"}]}
        self.assertEqual(len(prepare(ref, plan)["segments"]), 1)

    def test_hard_plan_and_hash_survive_translation(self):
        ref = reference([word("Question?", 0, .3), word("No!", .4, .7)])
        plan = {"note": "protected reversal", "boundaries": [{"before": {"parent_id": "p0", "word_index": 1}, "kind": "hard", "reason": "answer"}]}
        doc = prepare(ref, plan)
        for u, target in zip(doc["segments"], ["问", "答"]):
            u["target"] = target
            u.pop("break_before")  # The attached hard plan remains authoritative.
        doc["segments"][0].update(join_next=True, join_reason="proposed merge")
        result = compose(doc, ref)
        self.assertEqual(len(result["segments"]), 2)
        self.assertEqual(result["boundary_plan"], plan)
        self.assertEqual(result["meta"]["unified_cues"]["plan_sha256"], canonical_hash(plan))
        changed = copy.deepcopy(ref)
        changed["segments"][0]["asr_words"][0]["end"] = .31
        with self.assertRaisesRegex(ValueError, "hash"):
            compose(doc, changed)

    def test_no_plan_is_explicitly_not_semantic_review(self):
        result = prepare(reference(self.words))
        self.assertTrue(result["semantic_boundary_review_required"])
        self.assertFalse(result["meta"]["unified_cues"]["semantic_review_performed"])

    def test_source_issues_cannot_silently_lose_scope(self):
        ref = reference(self.words)
        ref["issues"] = [{"cue_ids": ["p0"], "detail": "unknown negation"}]
        with self.assertRaisesRegex(ValueError, "migrate"):
            prepare(ref)

    def test_offset_added_once(self):
        ref = reference([word("Hi", 1, 2)])
        ref["segments"][0]["asr_words_offset_ms"] = 10000
        c = prepare(ref)["segments"][0]
        self.assertEqual((c["start_ms"], c["end_ms"]), (11000, 12000))

    def test_missing_interior_word_preserved_without_interpolation(self):
        ref = reference([word("I", 0, .2), {"word": "missing"}, word("remain", .8, 1.1)])
        result = prepare(ref)
        self.assertEqual(result["coverage"]["words"], 3)
        self.assertEqual(result["segments"][0]["untimed_words"], [{"parent_id": "p0", "word_index": 1}])
        self.assertNotIn("start", ref["segments"][0]["asr_words"][1])
        with self.assertRaises(ValueError):
            prepare(reference([{"word": "first"}, word("end", .5, .8)]))

    def test_zero_word_can_join_and_reversed_word_rejected(self):
        result = prepare(reference([word("a", 0, 0), word("b", .1, .7)]))
        self.assertEqual(result["coverage"]["words"], 2)
        with self.assertRaises(ValueError):
            prepare(reference([word("bad", 1, .9)]))

    def test_compose_identity_without_approval_even_when_short(self):
        ref = reference([word("Q?", 0, .3), word("A!", .4, .7)], [1])
        doc = units_for(ref, ["问?", "答!"])
        before = copy.deepcopy(doc)
        result = compose(doc, ref)
        self.assertEqual(len(result["segments"]), 2)
        self.assertEqual([c["start_ms"] for c in result["segments"]], [0, 400])
        self.assertEqual(result["units"], doc["segments"])
        self.assertEqual(doc, before)
        self.assertEqual(text_sequence(result["segments"], "target"), "问?答!")

    def test_approved_join_and_issue_scope_preserved(self):
        ref = reference([word("I", 0, .3), word("agree", .4, 1.3)], [1])
        doc = units_for(ref, ["我", "同意"])
        doc["segments"][0].update(join_next=True, join_reason="same subject and predicate")
        doc["issues"] = [{"id": "I1", "cue_ids": ["u0", "u1"], "source_constraint": {"unknown": "tone", "material": True}}]
        original = copy.deepcopy(doc)
        result = compose(doc, ref)
        self.assertEqual(len(result["segments"]), 1)
        self.assertEqual(result["segments"][0]["unit_ids"], ["u0", "u1"])
        self.assertEqual(result["issues"][0]["unit_ids"], ["u0", "u1"])
        self.assertEqual(result["issues"][0]["cue_ids"], ["c000001"])
        self.assertEqual(result["issues"][0]["source_constraint"], doc["issues"][0]["source_constraint"])
        self.assertEqual(doc, original)
        self.assertEqual(compose(result, ref)["segments"], result["segments"])

    def test_break_before_vetoes_join(self):
        ref = reference([word("Q", 0, .3), word("A", .4, .7)], [1])
        doc = units_for(ref, ["问", "答"])
        doc["segments"][0].update(join_next=True, join_reason="claimed join")
        doc["segments"][1]["break_before"] = True
        self.assertEqual(len(compose(doc, ref)["segments"]), 2)

    def test_join_needs_reason_and_does_not_force_unreadable_merge(self):
        ref = reference([word("a", 0, 3), word("b", 3.1, 6.2)], [1])
        doc = units_for(ref, ["甲" * 40, "乙" * 40])
        doc["segments"][0]["join_next"] = True
        with self.assertRaises(ValueError):
            compose(doc, ref)
        doc["segments"][0]["join_reason"] = "continuous topic"
        self.assertEqual(len(compose(doc, ref)["segments"]), 2)

    def test_bad_ranges_fail_before_any_result(self):
        ref = reference([word("a", 0, .3), word("b", .4, .7)], [1])
        original = units_for(ref, ["甲", "乙"])
        for mutation in ("duplicate", "outside", "missing", "reverse"):
            doc = copy.deepcopy(original)
            if mutation == "duplicate":
                doc["segments"][1]["source_word_ranges"] = copy.deepcopy(doc["segments"][0]["source_word_ranges"])
            elif mutation == "outside":
                doc["segments"][0]["source_word_ranges"][0]["end_word_index_exclusive"] = 2
            elif mutation == "missing":
                doc["segments"].pop()
            else:
                doc["segments"].reverse()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                compose(doc, ref)

    def test_no_automatic_tail_and_reviewed_tail_is_separate(self):
        ref = reference([word("Yes", 0, .2), word("Next", 1, 2)], [1])
        doc = units_for(ref, ["对", "下一句"])
        self.assertEqual(compose(doc, ref)["segments"][0]["end_ms"], 200)
        doc["segments"][0]["tail_evidence"] = dict(reviewed=True, reason="reviewed pause", evidence="local interval 200-900", start_ms=200, end_ms=900, display_end_ms=800)
        c = compose(doc, ref)["segments"][0]
        self.assertEqual((c["speech_end_ms"], c["display_end_ms"], c["end_ms"]), (200, 800, 800))
        self.assertEqual(c["start_ms"], 0)

    def test_tail_rejects_unreviewed_unknown_and_next_onset(self):
        ref = reference([word("a", 0, .2), word("b", .7, 1)], [1])
        original = units_for(ref, ["甲", "乙"])
        evidence = dict(reviewed=True, reason="gap", evidence="clip", start_ms=200, end_ms=900, display_end_ms=800)
        for change in ({}, {"display_end_ms": 600, "reviewed": False}, {"display_end_ms": 600, "start_ms": 300}, {"display_end_ms": 1800, "end_ms": 2000}):
            doc = copy.deepcopy(original)
            doc["segments"][0]["tail_evidence"] = dict(evidence, **change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                compose(doc, ref)

    def test_wrap_preserves_wording_and_never_splits_display_units(self):
        text = "中文表达 " + "a_very_long_unbreakable_identifier" * 3 + " 保留全部文字"
        wrapped = wrap_text(text, 12)
        self.assertEqual("".join(text.split()), "".join(wrapped.split()))
        self.assertIn("a_very_long_unbreakable_identifier" * 3, wrapped)
        ref = reference([word("source", 0, .5)])
        result = compose(units_for(ref, ["中文" * 60]), ref)
        self.assertEqual(len(result["segments"]), 1)
        self.assertEqual(result["segments"][0]["target"], "中文" * 60)
        self.assertTrue(any(r["code"] == "line_width" for r in result["display_risks"]))

    def test_canonical_hash_and_new_output_only(self):
        self.assertEqual(canonical_hash({"a": 1, "b": 2}), canonical_hash({"b": 2, "a": 1}))
        base = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(prefix="unified-cues-test-", dir=base) as folder:
            self.assertTrue(Path(folder).resolve().is_relative_to(base))
            inp, out = Path(folder) / "reference.json", Path(folder) / "new.json"
            inp.write_text(json.dumps(reference(self.words)), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                main(["prepare", str(inp), "--output", str(out)])
            saved = out.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(["prepare", str(inp), "--output", str(out)])
            self.assertEqual(out.read_bytes(), saved)


if __name__ == "__main__":
    unittest.main()
