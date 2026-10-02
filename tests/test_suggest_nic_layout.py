import copy
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

import capture_inventory as ci
import suggest_nic_layout as sl

ROOT = Path(__file__).resolve().parent.parent


def nic(pcie, mac, **over):
    n = {"altname": None, "role": "unknown", "managed": False, "expected_link": False,
         "mac_address": mac, "pcie_id": pcie, "mtu": 1500, "ip_address": "0.0.0.0/0",
         "link_settings": {"speed": 50000, "autoneg": True}, "transceiver": {"brand": None}}
    n.update(over)
    return n


def captured():
    nics = [{"enp211s0f0np0": nic("d3:00.0", "b0:82:e2:37:37:5f", role="management", ip_address="10.0.0.5/20")}]
    nics += [{f"enp241s0f{i}np{i}": nic(f"f1:00.{i}", f"60:5e:65:5c:8b:e{i}")} for i in range(4)]
    nics += [{f"enp33s0f{i}np{i}": nic(f"21:00.{i}", f"54:9b:24:5d:34:2{i}")} for i in range(4)]
    return {"schema": {"name": "system_config", "version": 1},
            "system_info": {"hostname": "t", "network_renderer": "NetworkManager"},
            "nics": nics, "cameras": {}}


def names(cfg):
    return [next(iter(i)) for i in cfg["nics"]]


class LayoutTests(unittest.TestCase):
    def build(self, **kw):
        args = dict(cards={"f1:00": 1, "21:00": 2}, camera_ports=set(), camera_cards={1})
        args.update(kw)
        return sl.build_layout(captured(), **args)

    def test_names_altnames_and_subnets_follow_pancake0_convention(self):
        cfg = self.build()
        self.assertEqual(names(cfg), ["enp211s0f0np0"] + [f"mlnx{c}_p{p}_25g" for c in (1, 2) for p in (1, 2, 3, 4)])
        by = {k: v for i in cfg["nics"] for k, v in i.items()}
        self.assertEqual((by["mlnx1_p1_25g"]["altname"], by["mlnx1_p1_25g"]["ip_address"]), ("eth0", "192.168.110.1/24"))
        self.assertEqual((by["mlnx2_p4_25g"]["altname"], by["mlnx2_p4_25g"]["ip_address"]), ("eth7", "192.168.180.1/24"))
        self.assertEqual(by["mlnx2_p3_25g"]["pcie_id"], "21:00.2")  # p3 = function 2
        self.assertEqual(by["mlnx1_p1_25g"]["mtu"], 9000)
        self.assertEqual(by["mlnx1_p1_25g"]["link_settings"], {"speed": 25000, "autoneg": True})

    def test_roles_cameras_on_card_one_rest_spare_management_untouched(self):
        by = {k: v for i in self.build()["nics"] for k, v in i.items()}
        self.assertTrue(all(by[f"mlnx1_p{p}_25g"]["role"] == "camera" and by[f"mlnx1_p{p}_25g"]["managed"] for p in range(1, 5)))
        self.assertTrue(all(by[f"mlnx2_p{p}_25g"]["role"] == "spare" and not by[f"mlnx2_p{p}_25g"]["managed"] for p in range(1, 5)))
        self.assertEqual(by["enp211s0f0np0"]["role"], "management")
        self.assertIsNone(by["enp211s0f0np0"]["altname"])

    def test_individual_camera_ports(self):
        by = {k: v for i in self.build(camera_cards=set(), camera_ports={(1, 2), (2, 1)})["nics"] for k, v in i.items()}
        cams = sorted(k for k, v in by.items() if v["role"] == "camera")
        self.assertEqual(cams, ["mlnx1_p2_25g", "mlnx2_p1_25g"])

    def test_result_validates_and_names_fit_ifnamsiz(self):
        cfg = self.build()
        self.assertEqual(ci.validate(cfg), [])
        self.assertTrue(all(len(n) <= 15 for n in names(cfg)))

    def test_errors(self):
        with self.assertRaises(sl.LayoutError):
            self.build(cards={"aa:00": 1})
        with self.assertRaises(sl.LayoutError):
            self.build(camera_ports={(1, 9)}, camera_cards=set())
        with self.assertRaises(sl.LayoutError):
            self.build(camera_cards={7})
        with self.assertRaises(sl.LayoutError):
            self.build(cards={"f1:00": 1, "21:00": 1})

    def test_cli_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dest = Path(tmp) / "c.yml", Path(tmp) / "out.yml"
            src.write_text(yaml.safe_dump(captured()))
            dest.write_text("keep")
            with self.assertRaises(SystemExit):
                sl.main(["--card", "f1:00=1", "--file", str(src), "--output", str(dest)])
            self.assertEqual(dest.read_text(), "keep")
            self.assertEqual(sl.main(["--card", "f1:00=1", "--card", "21:00=2", "--camera-card", "1",
                                      "--file", str(src), "--output", str(dest), "--force"]), 0)
            self.assertIn("mlnx1_p1_25g", dest.read_text())


YQ_STUB = """#!/usr/bin/env python3
import sys, yaml, re
args = sys.argv[1:]
assert args[0] == "e"
q, f = args[1], args[2]
data = yaml.safe_load(sys.stdin if f == "-" else open(f)) or {}
nics = data.get("nics", [])
if q == ".test": print("null")
elif q == ".nics | length": print(len(nics))
elif m := re.fullmatch(r"\\.nics\\[(\\d+)\\] \\| keys \\| \\.\\[0\\]", q): print(next(iter(nics[int(m.group(1))])))
elif m := re.fullmatch(r"\\.nics\\[(\\d+)\\]\\.(\\w+)\\.(\\w+)", q):
    v = nics[int(m.group(1))][m.group(2)].get(m.group(3)); print("null" if v is None else v)
else: sys.exit(1)
"""


class UdevDryRunTests(unittest.TestCase):
    def run_script(self, cfg, *args):
        tmp = tempfile.mkdtemp()
        bin_dir = Path(tmp) / "bin"
        bin_dir.mkdir()
        yq = bin_dir / "yq"
        yq.write_text(YQ_STUB)
        yq.chmod(yq.stat().st_mode | stat.S_IXUSR)
        host_dir = Path(tmp) / "hosts" / "testhost"
        host_dir.mkdir(parents=True)
        (host_dir / "config.yml").write_text(yaml.safe_dump(cfg))
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "PANCAKEBATTER_HOST": "testhost"}
        # run a copy of the repo scripts next to our fake hosts/ dir
        for rel in ("network_alias_assignment.sh", "lib/host_config.sh"):
            dst = Path(tmp) / rel
            dst.parent.mkdir(exist_ok=True)
            dst.write_text((ROOT / rel).read_text())
        return subprocess.run(["bash", str(Path(tmp) / "network_alias_assignment.sh"), *args],
                              capture_output=True, text=True, env=env, timeout=30, stdin=subprocess.DEVNULL)

    def test_dry_run_prints_link_files_and_skips_unnamed_nics(self):
        cfg = sl.build_layout(captured(), {"f1:00": 1, "21:00": 2}, set(), {1})
        res = self.run_script(cfg, "--dry-run")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        out = res.stdout
        self.assertIn("10-pancakebatter-mlnx1_p1_25g.link", out)
        self.assertIn("PermanentMACAddress=60:5e:65:5c:8b:e0\n\n[Link]\nName=mlnx1_p1_25g\nAlternativeName=eth0\n", out)
        self.assertIn("PermanentMACAddress=54:9b:24:5d:34:23\n\n[Link]\nName=mlnx2_p4_25g\nAlternativeName=eth7\n", out)
        self.assertEqual(out.count("[Match]"), 8)
        self.assertIn("AlternativeNamesPolicy=database onboard slot path", out)
        self.assertIn("enp211s0f0np0 has no altname", out)
        self.assertNotIn("Name=enp211s0f0np0", out)
        self.assertNotIn("SYMLINK", out)
        self.assertNotIn("Rebooting", out)

    def test_unknown_argument_is_rejected(self):
        res = self.run_script(captured(), "--bogus")
        self.assertEqual(res.returncode, 1)


if __name__ == "__main__":
    unittest.main()
