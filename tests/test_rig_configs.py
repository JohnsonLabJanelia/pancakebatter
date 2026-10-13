"""rigs/<rig_id>/rig.yml: valid, derived parities match the rules, revision bumps pinned, host bindings join."""
import copy
import json
import unittest
from pathlib import Path

import yaml

import rigconfig as rc

REPO = Path(__file__).resolve().parent.parent
RIG_FILES = sorted((REPO / "rigs").glob("*/rig.yml"))
RIGS = {p.parent.name: yaml.safe_load(p.read_text()) for p in RIG_FILES}
HOST_CONFIGS = {p.parent.name: yaml.safe_load(p.read_text()) for p in sorted((REPO / "hosts").glob("*/config.yml"))}
SCHEMA = json.loads((REPO / "schemas" / "rig_config.v1.schema.json").read_text())


def path(**kw):
    p = {"projection_mode": "front", "projector_mount": "table", "projector_mount_rotation_deg_about_optical_axis": 0,
         "projector_firmware_flip": "none", "optical_mirror_count": 0}
    p.update(kw)
    return p


def camera(**kw):
    c = {"viewing_side": "dorsal", "optical_mirror_count": 0, "reverse_x": False, "reverse_y": False, "rotation_deg": 0}
    c.update(kw)
    return c


class RigDocumentTests(unittest.TestCase):
    def test_there_is_at_least_one_rig(self):
        self.assertTrue(RIG_FILES)

    def test_every_rig_validates_and_is_named_by_its_directory(self):
        for name, doc in RIGS.items():
            with self.subTest(rig=name):
                self.assertEqual(rc.validate(doc), [])
                self.assertEqual(doc["rig_id"], name)

    def test_every_rig_validates_against_the_json_schema_when_available(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        for name, doc in RIGS.items():
            with self.subTest(rig=name):
                jsonschema.Draft202012Validator(SCHEMA).validate(doc)

    def test_revision_bump_rule(self):
        """The physical digest of rig.yml must equal revisions.json's entry for its rig_geometry_revision.
        Change a physical field without bumping (and recording) the revision, and this fails."""
        for name, doc in RIGS.items():
            with self.subTest(rig=name):
                lock = json.loads((REPO / "rigs" / name / "revisions.json").read_text())
                self.assertEqual(lock["rig_id"], name)
                revs = sorted(int(r) for r in lock["revisions"])
                self.assertEqual(revs, list(range(revs[0], revs[-1] + 1)), "revisions must be contiguous")
                self.assertEqual(doc["rig_geometry_revision"], revs[-1], "rig.yml must be at the latest recorded revision")
                self.assertEqual(rc.physical_digest(doc), lock["revisions"][str(revs[-1])]["physical_sha256"],
                                 "physical fields changed: bump rig_geometry_revision and add a revisions.json entry")
                digests = [lock["revisions"][str(r)]["physical_sha256"] for r in revs]
                self.assertEqual(len(digests), len(set(digests)), "two revisions with the same physical state")

    def test_digest_ignores_free_text_and_tracks_physical_fields(self):
        doc = copy.deepcopy(next(iter(RIGS.values())))
        base = rc.physical_digest(doc)
        doc["description"] += " (edited)"
        doc["provenance"]["note"] = "changed"
        serial = next(iter(doc["cameras"]))
        doc["cameras"][serial]["mount_notes"] = "changed"
        self.assertEqual(rc.physical_digest(doc), base)
        doc["cameras"][serial]["viewing_side"] = "ventral"
        self.assertNotEqual(rc.physical_digest(doc), base)

    def test_stored_derived_value_that_disagrees_is_refused(self):
        doc = copy.deepcopy(next(iter(RIGS.values())))
        pid = next(iter(doc["projection_paths"]))
        doc["projection_paths"][pid]["derived"]["canvas_space_parity"] = 1
        self.assertTrue(any("canvas_space_parity" in e for e in rc.validate(doc)))

    def test_unknown_is_not_a_default_and_bad_values_are_refused(self):
        doc = copy.deepcopy(next(iter(RIGS.values())))
        serial = next(iter(doc["cameras"]))
        for field, bad in (("reverse_x", None), ("reverse_x", 0), ("viewing_side", "top"),
                           ("optical_mirror_count", -1), ("optical_mirror_count", True), ("rotation_deg", 45)):
            d = copy.deepcopy(doc)
            d["cameras"][serial][field] = bad
            with self.subTest(field=field, value=bad):
                self.assertTrue(rc.validate(d))


class ProjectorSettingsTests(unittest.TestCase):
    def doc(self, flip="unknown", **settings):
        doc = copy.deepcopy(next(iter(RIGS.values())))
        p = next(iter(doc["projection_paths"].values()))
        p["projector_firmware_flip"] = flip
        p["projector_settings"].update(settings)
        p["derived"]["canvas_space_parity"] = rc.canvas_space_parity(p)
        for serial, want in rc.derived(doc)["cameras"].items():
            doc["cameras"][serial]["derived"] = want
        return doc, p

    def test_required_and_in_the_digest(self):
        doc, p = self.doc()
        base = rc.physical_digest(doc)
        p["projector_settings"]["keystone_v"] = 0
        self.assertNotEqual(rc.physical_digest(doc), base)
        del p["projector_settings"]
        self.assertTrue(any("projector_settings: required" in e for e in rc.validate(doc)))

    def test_effective_position_fixes_the_firmware_flip(self):
        for pos, flip in rc.POSITION_FLIP.items():
            with self.subTest(position=pos):
                self.assertEqual(rc.validate(self.doc(flip, position_menu=pos, position_effective=pos)[0]), [])
                wrong = "vertical" if flip != "vertical" else "none"
                self.assertTrue(rc.validate(self.doc(wrong, position_menu=pos, position_effective=pos)[0]))

    def test_auto_menu_needs_the_effective_position_separately(self):
        self.assertEqual(rc.validate(self.doc(position_menu="auto")[0]), [])
        self.assertEqual(rc.validate(self.doc("horizontal", position_menu="auto", position_effective="rear")[0]), [])
        self.assertTrue(rc.validate(self.doc("both", position_menu="rear", position_effective="front_ceiling")[0]))

    def test_bad_values_are_refused(self):
        for key, bad in (("keystone_h", 31), ("keystone_v", None), ("digital_zoom_percent", 79),
                         ("digital_zoom_percent", True), ("position_menu", "Front Table"), ("aspect_ratio", "21:9")):
            with self.subTest(key=key, value=bad):
                self.assertTrue(rc.validate(self.doc(**{key: bad})[0]))


class ParityRuleTests(unittest.TestCase):
    def test_canvas_parity(self):
        self.assertEqual(rc.canvas_space_parity(path()), 1)
        self.assertEqual(rc.canvas_space_parity(path(projection_mode="rear")), -1)
        self.assertEqual(rc.canvas_space_parity(path(projection_mode="rear", optical_mirror_count=1)), 1)
        self.assertEqual(rc.canvas_space_parity(path(projector_firmware_flip="horizontal")), -1)
        self.assertEqual(rc.canvas_space_parity(path(projector_firmware_flip="both")), 1)
        # Rotations never reflect.
        self.assertEqual(rc.canvas_space_parity(path(projector_mount="ceiling",
                                                     projector_mount_rotation_deg_about_optical_axis=180)), 1)
        self.assertEqual(rc.canvas_space_parity(path(optical_mirror_count="unknown")), "unknown")
        self.assertEqual(rc.canvas_space_parity(path(projector_mount="unknown")), 1)

    def test_recorded_parity(self):
        self.assertEqual(rc.recorded_space_parity(camera()), 1)
        self.assertEqual(rc.recorded_space_parity(camera(viewing_side="ventral")), -1)
        self.assertEqual(rc.recorded_space_parity(camera(viewing_side="ventral", optical_mirror_count=1)), 1)
        self.assertEqual(rc.recorded_space_parity(camera(reverse_x=True)), -1)
        self.assertEqual(rc.recorded_space_parity(camera(reverse_x=True, reverse_y=True)), 1)
        self.assertEqual(rc.recorded_space_parity(camera(reverse_x=rc.NOT_SUPPORTED, reverse_y=rc.NOT_SUPPORTED)), 1)
        self.assertEqual(rc.recorded_space_parity(camera(rotation_deg=90)), 1)
        self.assertEqual(rc.recorded_space_parity(camera(reverse_y="unknown")), "unknown")

    def test_handedness(self):
        self.assertEqual(rc.handedness(1, 1), "preserved")
        self.assertEqual(rc.handedness(-1, -1), "preserved")
        self.assertEqual(rc.handedness(1, -1), "reflected")
        self.assertEqual(rc.handedness("unknown", 1), "unknown")


class HostBindingTests(unittest.TestCase):
    def test_host_bindings_name_existing_rigs_cameras_and_paths(self):
        for host, cfg in HOST_CONFIGS.items():
            with self.subTest(host=host):
                self.assertEqual(rc.validate_binding(host, cfg, RIGS), [])

    def test_every_rig_is_bound_to_exactly_one_host(self):
        bound = [b["rig_id"] for cfg in HOST_CONFIGS.values() for b in cfg.get("rigs") or []]
        self.assertEqual(sorted(bound), sorted(RIGS))

    def test_binding_errors_are_reported(self):
        cfg = copy.deepcopy(HOST_CONFIGS["pancake0"])
        cfg["rigs"][0]["display_outputs"][0]["projection_path"] = "nope"
        cfg["rigs"].append({"rig_id": "ghost", "display_outputs": [{"output_name": "DP-0", "projection_path": "main"}]})
        errs = rc.validate_binding("pancake0", cfg, RIGS)
        self.assertTrue(any("nope" in e for e in errs))
        self.assertTrue(any("ghost" in e for e in errs))
        self.assertTrue(any("not driven by any output" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
