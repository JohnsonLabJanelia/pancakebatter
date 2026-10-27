"""Append-only rig events (rig_events.py): setting changes with time windows, part conditions, corrections."""
import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

import rig_events as re_

REPO = Path(__file__).resolve().parent.parent
RIG = yaml.safe_load((REPO / "rigs" / "omnifin0" / "rig.yml").read_text())

def _at_ref(ref, relpath):
    """File contents at a git ref, or None (no git, unknown ref, or not in that tree)."""
    try:
        out = subprocess.run(["git", "-C", str(REPO), "show", f"{ref}:{relpath}"], capture_output=True, text=True,
                             timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


try:
    import jsonschema
except ImportError:
    jsonschema = None


def change(**kw):
    ev = {"event_id": "e1", "kind": "setting_changed", "item": "projector.benq.x3100i",
          "setting": "game_and_pic.brightness", "old": 11, "new": 50, "at_precision": "window",
          "not_before": "2026-10-24T20:17:50Z", "not_after": "2026-10-24T22:04:44Z", "recorded_at": "2026-10-27"}
    ev.update(kw)
    return ev


def observed(**kw):
    ev = {"event_id": "e2", "kind": "condition_observed", "condition_id": "c1", "layer": "shelf",
          "arenas": ["arena_2", "arena_3"], "condition": "scratches", "extent": "a few", "at": "2026-10-27",
          "at_precision": "on_or_before", "recorded_at": "2026-10-27"}
    ev.update(kw)
    return ev


def cleared(**kw):
    ev = {"event_id": "e3", "kind": "condition_cleared", "condition_id": "c1", "at": "2026-11-02",
          "at_precision": "exact", "recorded_at": "2026-11-02"}
    ev.update(kw)
    return ev


def doc(*events):
    return {"schema": {"name": "rig_events", "version": 1}, "rig": "omnifin0", "events": list(events)}


def fails(*events, rig_doc=RIG):
    return [m for level, m in re_.findings(doc(*events), "omnifin0", rig_doc, check_files=False) if level == "fail"]


class DerivationTests(unittest.TestCase):
    def test_window_setting_history(self):
        self.assertEqual(fails(change()), [])
        self.assertEqual(re_.setting_history([change()], "projector.benq.x3100i", "game_and_pic.brightness"),
                         [{"value": 11, "valid_until": "2026-10-24T20:17:50Z"},
                          {"value": 50, "valid_from": "2026-10-24T22:04:44Z"}])

    def test_conditions_open_and_close(self):
        self.assertEqual([e["condition_id"] for e in re_.active_conditions([observed()])], ["c1"])
        self.assertEqual(re_.active_conditions([observed()], ["arena_1", "arena_4"]), [])
        self.assertEqual(len(re_.active_conditions([observed()], ["arena_3"])), 1)
        self.assertEqual(fails(observed(), cleared()), [])
        self.assertEqual(re_.active_conditions([observed(), cleared()]), [])

    def test_correction_replaces_and_voids(self):
        fix = {"event_id": "e3", "kind": "correction", "corrects": "e2", "arenas": ["arena_2"],
               "reason": "only arena 2", "recorded_at": "2026-10-28"}
        self.assertEqual(fails(observed(), fix), [])
        self.assertEqual(re_.active_conditions([observed(), fix])[0]["arenas"], ["arena_2"])
        void = {"event_id": "e3", "kind": "correction", "corrects": "e2", "void": True, "reason": "r",
                "recorded_at": "2026-10-28"}
        self.assertEqual(re_.active_conditions([observed(), void]), [])


def attested(**kw):
    import rigconfig
    ev = {"event_id": "e3", "kind": "attested", "fields": ["room_orientation"],
          "rig_geometry_revision": RIG["rig_geometry_revision"], "orientation_sha256": rigconfig.orientation_digest(RIG),
          "method": "crosshair", "at_precision": "window", "not_before": "2026-10-27T22:24:50Z",
          "not_after": "2026-10-27T22:29:43Z", "recorded_at": "2026-10-27"}
    ev.update(kw)
    return ev


class AttestationTests(unittest.TestCase):
    def test_current_until_the_orientation_fields_change(self):
        self.assertEqual(fails(attested()), [])
        self.assertEqual(len(re_.current_attestations([attested()], RIG, "room_orientation")), 1)
        moved = copy.deepcopy(RIG)
        next(iter(moved["cameras"].values()))["rotation_deg"] = 90
        self.assertEqual(re_.current_attestations([attested()], moved, "room_orientation"), [])
        warns = [m for lvl, m in re_.findings(doc(attested()), "omnifin0", moved, check_files=False) if lvl == "warn"]
        self.assertTrue(any("no current attestation" in m for m in warns))
        picture = copy.deepcopy(RIG)
        next(iter(picture["projection_paths"].values()))["projector_settings"]["brightness"] = 7
        self.assertEqual(len(re_.current_attestations([attested()], picture, "room_orientation")), 1)

    def test_bad_attestations_are_refused(self):
        for kw, needle in ((dict(fields=["media_stack"]), "fields must be"),
                           (dict(orientation_sha256="abc"), "64 lowercase hex"),
                           (dict(rig_geometry_revision=99), "newer than rig.yml"),
                           (dict(method=""), "method")):
            with self.subTest(kw=kw):
                self.assertTrue(any(needle in m for m in fails(attested(**kw))))


class CheckTests(unittest.TestCase):
    def test_bad_events_are_refused(self):
        cases = (
            ([change(at="2026-10-24")], "not at"),
            ([change(not_after=None)], "not_after must be"),
            ([change(not_before="2026-10-25T00:00:00Z")], "is after not_after"),
            ([change(at_precision="exact", at="2026-10-24")], "belong on at_precision window"),
            ([change(new=11)], "both 11"),
            ([change(colour="red")], "unexpected keys"),
            ([{k: v for k, v in change().items() if k != "setting"}], "needs setting"),
            ([observed(arenas=["arena_9"])], "not in rig.yml media_stack.arenas"),
            ([observed(layer="gasket")], "not a rig.yml media_stack layer id"),
            ([observed(condition_id="scratch")], "condition_id c1"),
            ([cleared(event_id="e1")], "not open"),
            ([observed(), observed(event_id="e3")], "opened twice"),
            ([change(recorded_at="2026-10-28"), observed()], "appended, never inserted"),
            ([observed(evidence_files=[{"path": "x", "sha256": "abc"}])], "64 lowercase hex"),
            ([observed(), {"event_id": "e3", "kind": "correction", "corrects": "e2", "condition_id": "c2",
                           "reason": "r", "recorded_at": "2026-10-27"}], "unexpected keys"),
        )
        for events, needle in cases:
            with self.subTest(needle=needle):
                found = fails(*events)
                self.assertTrue(any(needle in m for m in found), found)

    def test_header(self):
        self.assertTrue(re_.findings(dict(doc(), rig="dumpling"), "omnifin0"))
        self.assertTrue(re_.findings(dict(doc(), schema={"name": "host_events", "version": 1}), "omnifin0"))

    def test_evidence_file_hash_is_checked_when_reachable(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "record.json"
            f.write_text("{}")
            good = {"path": str(f), "sha256": hashlib.sha256(b"{}").hexdigest()}
            ev = observed(evidence_files=[good])
            self.assertEqual(re_.findings(doc(ev), "omnifin0", RIG), [])
            f.write_text("{changed}")
            self.assertTrue(any("has sha256" in m for _, m in re_.findings(doc(ev), "omnifin0", RIG)))
        missing = observed(evidence_files=[{"path": "/nonexistent/x", "sha256": "0" * 64}])
        self.assertEqual(re_.findings(doc(missing), "omnifin0", RIG), [])  # unreachable: shape only


class RepoFileTests(unittest.TestCase):
    def test_repo_rig_event_files_are_valid(self):
        self.assertEqual(re_.check_all(check_files=False)[0], [])

    @unittest.skipUnless(jsonschema, "jsonschema not installed")
    def test_rig_event_files_match_the_json_schema(self):
        schema = json.loads((REPO / "schemas" / "rig_events.v1.schema.json").read_text())
        for path in sorted((REPO / "rigs").glob("*/events.yml")):
            with self.subTest(rig=path.parent.name):
                jsonschema.Draft202012Validator(schema).validate(yaml.safe_load(path.read_text()))

    def test_append_only_against_origin_main(self):
        for path in sorted((REPO / "rigs").glob("*/events.yml")):
            rel = str(path.relative_to(REPO))
            old_text = _at_ref("origin/main", rel)
            if old_text is None:
                continue
            old = yaml.safe_load(old_text).get("events") or []
            new = yaml.safe_load(path.read_text()).get("events") or []
            with self.subTest(file=rel):
                self.assertEqual(new[:len(old)], old, "an existing event was edited, removed or had one inserted "
                                                      "before it; append a correction instead")


if __name__ == "__main__":
    unittest.main()
