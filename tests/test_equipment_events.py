"""Append-only equipment events (equipment_events.py): checks, corrections, derived location, history,
and that events already on origin/main are never edited, removed or inserted before."""
import copy
import json
import subprocess
import unittest
from pathlib import Path

import yaml

import equipment_events as ee
import instruments

REPO = Path(__file__).resolve().parent.parent

try:
    import jsonschema
except ImportError:
    jsonschema = None


def installed(**kw):
    ev = {"event_id": "e1", "kind": "installed", "at": "2026-10-19", "at_precision": "on_or_before",
          "to": "pancake0", "recorded_at": "2026-10-24"}
    ev.update(kw)
    return ev


def removed(**kw):
    ev = {"event_id": "e2", "kind": "removed", "at": "2026-10-24", "at_precision": "exact", "from": "pancake0",
          "to": "owner:Frank Loesche (Janelia MCN NET)", "recorded_at": "2026-10-24"}
    ev.update(kw)
    return ev


def fails(events, known=None):
    return [m for level, m in ee.findings("items.x", events, known) if level == "fail"]


class DerivationTests(unittest.TestCase):
    def test_current_location_and_history(self):
        events = [installed(rig="omnifin0"), removed()]
        self.assertEqual(fails(events), [])
        self.assertEqual(ee.current_location(events), "owner:Frank Loesche (Janelia MCN NET)")
        self.assertEqual(ee.history(events), [
            {"location": "pancake0", "rig": "omnifin0", "since": "2026-10-19", "since_precision": "on_or_before",
             "until": "2026-10-24", "until_precision": "exact"},
            {"location": "owner:Frank Loesche (Janelia MCN NET)", "since": "2026-10-24", "since_precision": "exact",
             "until": None, "until_precision": None}])
        self.assertIsNone(ee.current_location([]))

    def test_backfilled_earlier_event_sorts_by_physical_time(self):
        # Discovered later that it sat in storage before the rig: appended last, but earliest in time.
        events = [installed(), removed(),
                  {"event_id": "e3", "kind": "moved", "at": "2026-09-01", "at_precision": "on_or_after",
                   "to": "storage:cabinet_3", "recorded_at": "2026-10-25"}]
        self.assertEqual(fails(events), [])
        self.assertEqual([s["location"] for s in ee.history(events)],
                         ["storage:cabinet_3", "pancake0", "owner:Frank Loesche (Janelia MCN NET)"])

    def test_void_and_replacement_corrections(self):
        void = {"event_id": "e3", "kind": "correction", "corrects": "e2", "void": True,
                "reason": "never left", "recorded_at": "2026-10-25"}
        events = [installed(), removed(), void]
        self.assertEqual(fails(events), [])
        self.assertEqual(ee.current_location(events), "pancake0")
        fix = {"event_id": "e3", "kind": "correction", "corrects": "e2", "to": "storage:cabinet_3",
               "reason": "went to the cabinet, not back to Frank", "recorded_at": "2026-10-25"}
        events = [installed(), removed(), fix]
        self.assertEqual(fails(events), [])
        self.assertEqual(ee.current_location(events), "storage:cabinet_3")
        self.assertEqual(events[1]["to"], "owner:Frank Loesche (Janelia MCN NET)")  # the original is untouched

    def test_attachments(self):
        attach = {"event_id": "e2", "kind": "attached", "at": "2026-10-19", "at_precision": "on_or_before",
                  "with": "spectrometer-qepro-1", "recorded_at": "2026-10-24"}
        events = [installed(), attach]
        self.assertEqual(fails(events, {"spectrometer-qepro-1"}), [])
        self.assertEqual(ee.attached_to(events), {"spectrometer-qepro-1"})
        detach = dict(attach, event_id="e3", kind="detached", at="2026-10-24", at_precision="exact")
        self.assertEqual(ee.attached_to(events + [detach]), set())


class CheckTests(unittest.TestCase):
    def test_bad_events_are_refused(self):
        attach = {"event_id": "e2", "kind": "attached", "at": "2026-10-19", "at_precision": "exact",
                  "with": "nope", "recorded_at": "2026-10-24"}
        cases = (
            ([installed(), installed()], "used twice"),
            ([installed(event_id="1")], "event_id"),
            ([installed(kind="lost")], "kind must be"),
            ([installed(at="Oct 19")], "at must be"),
            ([installed(at_precision="about")], "at_precision"),
            ([installed(at="2026-10-25")], "after recorded_at"),
            ([installed(recorded_at="2026-10-25"), removed()], "appended, never inserted"),
            ([{k: v for k, v in installed().items() if k != "to"}], "needs to"),
            ([installed(to="Frank's desk")], "owner:<who>"),
            ([installed(), removed(**{"from": "dumpling"})], "from 'dumpling' but it was at 'pancake0'"),
            ([installed(), attach], "not a known item"),
            ([installed(), dict(attach, kind="detached", **{"with": "x"})], "not attached to"),
            ([installed(**{"with": "x"})], "with belongs on"),
            ([installed(), {"event_id": "e2", "kind": "correction", "corrects": "e9", "void": True,
                            "reason": "r", "recorded_at": "2026-10-24"}], "earlier event_id"),
            ([installed(), {"event_id": "e2", "kind": "correction", "corrects": "e1", "void": True,
                            "recorded_at": "2026-10-24"}], "reason or evidence"),
            ([installed(), {"event_id": "e2", "kind": "correction", "corrects": "e1", "void": True, "to": "dumpling",
                            "reason": "r", "recorded_at": "2026-10-24"}], "either void"),
            ([installed(), {"event_id": "e2", "kind": "correction", "corrects": "e1", "at": "2026-12-01",
                            "reason": "r", "recorded_at": "2026-10-24"}], "after recorded_at"),
            ([installed(colour="red")], "unexpected keys"),
        )
        for events, needle in cases:
            with self.subTest(needle=needle):
                found = fails(events, {"x"})
                self.assertTrue(any(needle in m for m in found), found)

    def test_shared_current_location_must_match_the_events(self):
        doc = yaml.safe_load((REPO / "equipment" / "shared.yml").read_text())
        self.assertEqual(instruments.validate_shared(doc)[0], [])
        bad = copy.deepcopy(doc)
        bad["items"][0]["current_location"] = "dumpling"
        errors = instruments.validate_shared(bad)[0]
        self.assertTrue(any("events put it at" in e for e in errors), errors)
        no_events = copy.deepcopy(bad)
        del no_events["items"][0]["events"]  # events are optional per item in v1
        self.assertEqual(instruments.validate_shared(no_events)[0], [])

    def test_host_events_header_and_inventory(self):
        doc = {"schema": {"name": "host_events", "version": 1}, "host": "h1",
               "components": [{"serial_number": "S1", "component": "storage_device", "events": [installed(to="h1")]}]}
        config = {"storage_devices": [{"serial_number": "S1"}]}
        self.assertEqual(ee.host_findings(doc, "h1", config), [])
        self.assertTrue(any("host must be" in m for _, m in ee.host_findings(doc, "h2", config)))
        missing = ee.host_findings(doc, "h1", {"storage_devices": []})
        self.assertEqual([lvl for lvl, _ in missing], ["warn"])
        gone = copy.deepcopy(doc)
        gone["components"][0]["events"].append(removed(**{"from": "h1"}))
        self.assertTrue(any("still lists it" in m for lvl, m in ee.host_findings(gone, "h1", config) if lvl == "warn"))


class RepoFileTests(unittest.TestCase):
    def test_repo_event_files_are_valid(self):
        self.assertEqual(ee.check_all()[0], [])
        self.assertEqual(instruments.validate_shared(instruments.load_shared())[0], [])

    def test_schema_copies_match(self):
        shared = json.loads((REPO / "schemas" / "shared_equipment.v1.schema.json").read_text())["$defs"]
        host = json.loads((REPO / "schemas" / "host_events.v1.schema.json").read_text())["$defs"]
        for name in ("event", "date"):
            with self.subTest(name=name):
                self.assertEqual(host[name], shared[name])

    @unittest.skipUnless(jsonschema, "jsonschema not installed")
    def test_host_event_files_match_the_json_schema(self):
        schema = json.loads((REPO / "schemas" / "host_events.v1.schema.json").read_text())
        for path in sorted((REPO / "hosts").glob("*/events.yml")):
            with self.subTest(host=path.parent.name):
                jsonschema.Draft202012Validator(schema).validate(yaml.safe_load(path.read_text()))


def _at_ref(ref, relpath):
    """File contents at a git ref, or None (no git, unknown ref, or not in that tree)."""
    try:
        out = subprocess.run(["git", "-C", str(REPO), "show", f"{ref}:{relpath}"], capture_output=True, text=True,
                             timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _event_lists(doc, list_key, id_key):
    return {str(entry.get(id_key)): entry.get("events") or []
            for entry in (doc or {}).get(list_key) or [] if isinstance(entry, dict)}


class AppendOnlyTests(unittest.TestCase):
    """Events already on origin/main must still be the first events of each list, byte for byte."""

    BASE = "origin/main"

    def check(self, relpath, list_key, id_key):
        old_text = _at_ref(self.BASE, relpath)
        if old_text is None:
            self.skipTest(f"{relpath} not on {self.BASE} (or no git)")
        old = _event_lists(yaml.safe_load(old_text), list_key, id_key)
        new = _event_lists(yaml.safe_load((REPO / relpath).read_text()), list_key, id_key)
        for key, old_events in old.items():
            with self.subTest(file=relpath, entry=key):
                if old_events:
                    self.assertIn(key, new, f"{key} had events on {self.BASE} and was removed")
                self.assertEqual(new.get(key, [])[:len(old_events)], old_events,
                                 "an existing event was edited, removed or had one inserted before it; "
                                 "append a correction instead")

    def test_shared_events_are_append_only(self):
        self.check("equipment/shared.yml", "items", "id")

    def test_host_events_are_append_only(self):
        for path in sorted((REPO / "hosts").glob("*/events.yml")):
            self.check(str(path.relative_to(REPO)), "components", "serial_number")


if __name__ == "__main__":
    unittest.main()
