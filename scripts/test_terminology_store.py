"""Behavioral tests; all writes stay in isolated temporary libraries."""
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid
from unittest.mock import patch

import terminology_store as store


def addition(term_id="new-map", domain="sc2", **changes):
    return {"id": term_id, "domain": domain, "source": "Test Map", "target": "测试地图",
            "sense": "test-only map", "aliases": [], "status": "confirmed", "reusable": True,
            "sources": [{"url": "https://example.org/map", "checked_at": "2026-10-28",
                         "note": "Synthetic fixture only; never promoted to the real library"}],
            "decision_note": "Synthetic test fixture", **changes}


class StoreTests(unittest.TestCase):
    def setUp(self):
        base = Path(os.environ.get("TERMINOLOGY_TEST_TMP", tempfile.gettempdir())).resolve()
        self.temp = base / ("termstore-" + uuid.uuid4().hex)
        self.temp.mkdir(parents=True)
        def cleanup():
            resolved = self.temp.resolve()
            if resolved.parent != base or not resolved.name.startswith("termstore-"):
                raise ValueError("Refusing cleanup outside the isolated test directory")
            shutil.rmtree(resolved)
        self.addCleanup(cleanup)
        self.root = self.temp / "libraries"

    def load(self, domain="sc2", locale="zh-CN", profile="commentary"):
        return store.snapshot(self.root, [domain], "en", locale, profile)

    def test_s15_seed_and_existing_library_survive_next_project(self):
        first = self.load()
        self.assertEqual(len(first["libraries"][0]["entries"]), 330)
        result = store.promote(self.root, first, [addition()])
        self.assertEqual(result["results"][0]["status"], "added")
        second = self.load()
        self.assertEqual(len(second["libraries"][0]["entries"]), 331)
        self.assertEqual(second["libraries"][0]["revision"], 2)
        self.assertEqual(len(first["libraries"][0]["entries"]), 330)
        self.assertEqual(store.read(store.slot(self.root, first["libraries"][0]) / "v000001.json")["revision"], 1)

    def test_region_and_register_are_isolated(self):
        self.assertEqual(len(self.load()["libraries"][0]["entries"]), 330)
        self.assertEqual(self.load(locale="zh-TW")["libraries"][0]["entries"], [])
        self.assertEqual(self.load(profile="formal")["libraries"][0]["entries"], [])

    def test_unknown_domain_accumulates_without_catalog_edit(self):
        first = self.load(domain="economics")
        store.promote(self.root, first, [addition(domain="economics")])
        self.assertEqual(len(self.load(domain="economics")["libraries"][0]["entries"]), 1)
        self.assertEqual(len(self.load()["libraries"][0]["entries"]), 330)

    def test_multiple_domains(self):
        result = store.snapshot(self.root, ["sc2", "economics"], "en", "zh-CN", "commentary")
        self.assertEqual([len(x["entries"]) for x in result["libraries"]], [330, 0])

    def test_proposed_private_and_reference_entries_do_not_accumulate(self):
        first = self.load()
        cases = [addition(status="proposed"), addition(reusable=False), addition(sources=[]),
                 addition(origin="benchmark"), addition(sources=[{"url": "G:/private.txt", "checked_at": "today", "note": "private"}])]
        result = store.promote(self.root, first, cases)
        self.assertTrue(all(x["status"] == "skipped" for x in result["results"]))
        self.assertEqual(self.load()["libraries"][0]["revision"], 1)

    def test_exact_retry_is_idempotent_and_does_not_copy_project_fields(self):
        first = self.load()
        e = addition(occurrences=["private-video-cue"], private_context="private")
        store.promote(self.root, first, [e])
        retry = store.promote(self.root, first, [e])
        self.assertEqual(retry["results"][0]["status"], "unchanged")
        final = self.load()["libraries"][0]
        self.assertEqual(final["revision"], 2)
        self.assertNotIn("occurrences", final["entries"][-1])
        self.assertNotIn("private_context", final["entries"][-1])

    def test_conflicting_target_is_reported_without_blocking_unrelated_new_term(self):
        base = self.load()
        old = base["libraries"][0]["entries"][0]
        changed = addition(**{**old, "target": "conflicting"})
        result = store.promote(self.root, base, [changed, addition()])
        self.assertEqual([r["status"] for r in result["results"]], ["conflict", "added"])
        self.assertEqual(self.load()["libraries"][0]["entries"][0]["target"], old["target"])

    def test_explicit_term_update_and_stale_conflict(self):
        base = self.load()
        old = base["libraries"][0]["entries"][0]
        changed = addition(**{**old, "target": "Test choice", "replaces_target": old["target"], "change_reason": "test override"})
        self.assertEqual(store.promote(self.root, base, [changed])["results"][0]["status"], "updated")
        changed["target"] = "Stale choice"
        self.assertEqual(store.promote(self.root, base, [changed])["results"][0]["status"], "conflict")

    def test_parallel_project_new_terms_merge_without_lost_updates(self):
        a, b = self.load(), self.load()
        store.promote(self.root, a, [addition()])
        store.promote(self.root, b, [addition("other", source="Other Map")])
        self.assertEqual(len(self.load()["libraries"][0]["entries"]), 332)

    def test_duplicate_meaning_has_conflict_but_distinct_sense_allowed(self):
        base = self.load()
        store.promote(self.root, base, [addition()])
        second = self.load()
        result = store.promote(self.root, second, [addition("other-id"), addition("new-sense", sense="different sense")])
        self.assertEqual([r["status"] for r in result["results"]], ["conflict", "added"])

    def test_lock_prevents_overwrite_and_path_escape_is_rejected(self):
        base = self.load()
        directory = store.slot(self.root, base["libraries"][0])
        with store.locked(directory):
            with self.assertRaisesRegex(ValueError, "busy"):
                store.promote(self.root, base, [addition()])
        with self.assertRaises(ValueError):
            self.load(domain="../other")
        with self.assertRaisesRegex(ValueError, "different persistent root"):
            store.promote(self.root / "other", base, [addition()])

    def test_config_is_reusable_and_env_and_explicit_roots_override_it(self):
        conf = self.temp / "config.json"
        with patch.object(store, "config_path", return_value=conf), patch.dict(os.environ, {}, clear=True):
            store.atomic_write(conf, {"library_root": str(self.root)})
            self.assertEqual(store.root_path(), self.root)
            with patch.dict(os.environ, {"LONG_VIDEO_SUBTITLES_TERMS": str(self.root / "env")}):
                self.assertEqual(store.root_path(), self.root / "env")
                self.assertEqual(store.root_path(str(self.root / "explicit")), self.root / "explicit")


if __name__ == "__main__":
    unittest.main()
