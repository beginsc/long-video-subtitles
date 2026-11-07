import copy
import unittest
from source_guard import audit, content_fingerprint
from subtitles import check, export, assemble


def sample():
    doc = {"schema_version": 1, "timing_mode": "rebuild", "segments": [
        {"id": "u1", "start_ms": 0, "end_ms": 2000, "source": "I recall, wait, did she ...",
         "target": "我记得 等等", "parent_ids": ["p1"],
         "source_word_ranges": [{"parent_id": "p1", "start_word_index": 0, "end_word_index_exclusive": 7}]}],
        "issues": [{"id": "q1", "cue_ids": ["u1"], "unit_ids": ["u1"], "status": "provisional",
                    "detail": "Missing predicate", "source_constraint": {
                        "unknown": "predicate", "safe_content": "I recall, wait",
                        "forbidden_inference": ["inventing an action"], "risk": "predicate", "material": True},
                    "disposition": {"action": "safe_partial", "translator": "translator-A",
                                    "reviewer": "reviewer-B", "rationale": "Only known preface translated",
                                    "evidence": "Source text only; missing predicate unverified"}}]}
    sign(doc)
    return doc


def sign(doc):
    doc["issues"][0]["disposition"]["reviewed_sha256"] = content_fingerprint(doc, doc["issues"][0])


class GuardTests(unittest.TestCase):
    def test_partial_draft_only(self):
        doc = sample()
        self.assertTrue(check(doc)["ok"])
        self.assertFalse(check(doc, status="final")["ok"])
        self.assertIn("我记得", export(doc, "srt"))

    def test_hold_never_exports(self):
        doc = sample()
        doc["issues"][0]["disposition"] = {"action": "hold"}
        self.assertIn("guessed text", " ".join(audit(doc)))
        doc["segments"][0]["target"] = ""
        self.assertIn("blocks subtitle export", " ".join(audit(doc)))
        with self.assertRaises(ValueError):
            export(doc, "srt")

    def test_changed_semantics_invalidate_review(self):
        for field, value in (("source", "different source"), ("target", "她喜欢自己"),
                             ("source_word_ranges", [])):
            doc = sample()
            doc["segments"][0][field] = value
            self.assertIn("stale", " ".join(audit(doc)))
        doc = sample()
        doc["issues"][0]["source_constraint"]["unknown"] = "changed"
        self.assertIn("stale", " ".join(audit(doc)))

    def test_layout_does_not_invalidate_review(self):
        doc = sample()
        doc["segments"][0]["target"] = "我记得\n等等"
        doc["segments"][0]["end_ms"] = 2100
        self.assertEqual(audit(doc), [])

    def test_independent_review_required(self):
        doc = sample()
        doc["issues"][0]["disposition"]["reviewer"] = "translator-A"
        self.assertIn("independent", " ".join(audit(doc)))

    def test_unknown_polarity_not_neutralized(self):
        doc = sample()
        doc["issues"][0]["source_constraint"]["risk"] = "polarity"
        sign(doc)
        self.assertIn("polarity", " ".join(audit(doc)))

    def test_repair_requires_evidence_and_resolved_state(self):
        doc = sample()
        issue = doc["issues"][0]
        issue["disposition"]["action"] = "verified_repair"
        self.assertTrue(audit(doc))
        issue["status"] = "resolved"
        issue["disposition"]["evidence"] = ""
        sign(doc)
        self.assertTrue(audit(doc))
        issue["disposition"]["evidence"] = "Reviewer checked supplied source evidence record"
        sign(doc)
        self.assertEqual(audit(doc), [])  # Declarations checked; truth is still a human/model review task.

    def test_legacy_unresolved_guess_blocked(self):
        doc = sample()
        del doc["issues"][0]["source_constraint"]
        self.assertIn("needs source_constraint", " ".join(audit(doc)))
        self.assertFalse(check(doc)["ok"])

    def test_clean_colloquial_fragment_not_blanket_blocked(self):
        doc = sample()
        doc["issues"] = []
        doc["segments"][0].update(source="What? Him?", target="什么 他?")
        self.assertEqual(audit(doc), [])

    def test_composed_edit_cannot_hide_behind_old_units(self):
        doc = sample()
        doc["units"] = copy.deepcopy(doc["segments"])
        doc["segments"][0].update(id="c1", unit_ids=["u1"])
        doc["issues"][0]["cue_ids"] = ["c1"]
        self.assertEqual(audit(doc), [])
        doc["segments"][0]["target"] = "她喜欢自己"
        self.assertIn("differs", " ".join(audit(doc)))

    def test_scope_and_composed_coverage(self):
        doc = sample()
        doc["issues"][0]["unit_ids"] = ["missing"]
        self.assertIn("unknown", " ".join(audit(doc)))
        doc = sample()
        doc["units"] = copy.deepcopy(doc["segments"])
        doc["segments"][0]["unit_ids"] = ["u1", "u1"]
        self.assertIn("coverage", " ".join(audit(doc)))

    def test_uncomposed_issue_cannot_bind_a_different_cue(self):
        doc = sample()
        doc['issues'][0]['cue_ids'] = ['different']
        self.assertIn('scope', ' '.join(audit(doc)))

    def test_polarity_aliases_do_not_bypass(self):
        for risk in ['polarity ', 'Polarity', 'polarity_uncertain']:
            doc = sample()
            doc['issues'][0]['source_constraint']['risk'] = risk
            sign(doc)
            self.assertIn('polarity', ' '.join(audit(doc)))

    def test_review_declarations_are_bound(self):
        for key in ['evidence', 'rationale', 'reviewer']:
            doc = sample()
            doc['issues'][0]['disposition'][key] = 'changed after review'
            self.assertIn('stale', ' '.join(audit(doc)))

    def test_chinese_wrap_is_not_semantic_edit(self):
        doc = sample()
        doc['segments'][0]['target'] = '我\n记得 等等'
        self.assertEqual(audit(doc), [])

    def test_composed_word_mapping_is_bound(self):
        doc = sample()
        doc['units'] = copy.deepcopy(doc['segments'])
        doc['segments'][0]['unit_ids'] = ['u1']
        doc['segments'][0]['source_word_ranges'][0]['start_word_index'] = 1
        self.assertIn('word ranges differ', ' '.join(audit(doc)))

    def test_unmigrated_parallel_constraints_block(self):
        doc = sample()
        doc['source_constraints'] = [{'cue_ids':['u1'], 'unknown':'predicate'}]
        self.assertIn('single issues', ' '.join(audit(doc)))

    def test_assembly_preserves_boundary_and_engine_provenance(self):
        doc = sample()
        doc['issues'] = []
        doc['timing_mode'] = 'fixed'
        doc['meta'] = {'unified_cues':{'engine_version':'test-engine'}}
        doc['boundary_plan'] = {'boundaries':[{'before':{'parent_id':'p1','word_index':0},'kind':'hard','reason':'new speaker'}]}
        result = assemble([doc], copy.deepcopy(doc), 'fixed')
        self.assertEqual(result['boundary_plan'], doc['boundary_plan'])
        self.assertEqual(result['meta']['unified_cues'],doc['meta']['unified_cues'])
        self.assertEqual(result['meta']['assembly_parts'],[doc['meta']])


if __name__ == "__main__":
    unittest.main()
