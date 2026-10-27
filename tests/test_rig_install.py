"""rig_install.py: what install_host_config.sh installs under /etc/pancakebatter/rigs/ (run here into a temp dir)."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

import rig_install as ri

REPO = Path(__file__).resolve().parent.parent
HOST_CONFIGS = {p.parent.name: yaml.safe_load(p.read_text()) for p in sorted((REPO / "hosts").glob("*/config.yml"))}
BOUND = {h: c for h, c in HOST_CONFIGS.items() if c.get("rigs")}


class PlanTests(unittest.TestCase):
    def test_every_bound_host_plans_cleanly(self):
        self.assertTrue(BOUND)
        for host, cfg in BOUND.items():
            with self.subTest(host=host), mock.patch.object(ri, "_git", return_value=""):
                items, errors = ri.plan(host, cfg)
                self.assertEqual(errors, [])
                self.assertEqual([i["rig_id"] for i in items], ri.bound_rigs(cfg))

    def test_refusals(self):
        host, cfg = next(iter(BOUND.items()))
        with mock.patch.object(ri, "_git", return_value=" M rigs/x/rig.yml"):
            self.assertTrue(any("uncommitted" in e for e in ri.plan(host, cfg)[1]))
            self.assertEqual(ri.plan(host, cfg, allow_dirty=True)[1], [])
        with mock.patch.object(ri, "_git", return_value=None):
            self.assertTrue(any("cannot read git status" in e for e in ri.plan(host, cfg)[1]))
        bad = copy.deepcopy(cfg)
        bad["rigs"].append({"rig_id": "nosuchrig"})
        with mock.patch.object(ri, "_git", return_value=""):
            self.assertTrue(any("missing" in e for e in ri.plan(host, bad)[1]))


class InstallTests(unittest.TestCase):
    def test_install_sidecar_check_and_stale_removal(self):
        host, cfg = next(iter(BOUND.items()))
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(ri, "_git", side_effect=lambda repo, *a:
                                                                    "f" * 40 if a[0] == "rev-parse" else ""):
            dest = Path(tmp) / "rigs"
            dest.mkdir()
            (dest / "oldrig.yml").write_text("x")
            (dest / "oldrig.yml.source").write_text("x")
            (dest / "notes.txt").write_text("left alone")
            items, errors = ri.plan(host, cfg)
            self.assertEqual(errors, [])
            log = ri.install(items, dest)
            self.assertTrue(any("removed" in line and "oldrig.yml" in line for line in log))
            self.assertFalse((dest / "oldrig.yml").exists())
            self.assertTrue((dest / "notes.txt").exists())
            for item in items:
                rid = item["rig_id"]
                installed = dest / f"{rid}.yml"
                self.assertEqual(installed.read_bytes(), item["source"].read_bytes())
                side = yaml.safe_load((dest / f"{rid}.yml.source").read_text())
                self.assertEqual(side["commit"], "f" * 40)
                self.assertEqual(side["sha256"], ri._sha256(installed))
                self.assertEqual(side["rig_geometry_revision"], item["rig_geometry_revision"])
                self.assertEqual("events_sha256" in side, item["events"] is not None)
                self.assertEqual(yaml.safe_load(installed.read_text())["rig_id"], rid)
            self.assertEqual(ri.check(cfg, dest), [])
            (dest / f"{items[0]['rig_id']}.yml").write_text("edited")
            self.assertTrue(any("differs" in p for p in ri.check(cfg, dest)))


if __name__ == "__main__":
    unittest.main()
