import argparse
import copy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import camera_adopt as ca


def nic(ip, role="camera", managed=True):
    return {"altname": None, "role": role, "managed": managed, "expected_link": managed,
            "mac_address": "aa:bb:cc:dd:ee:01", "mtu": 9000, "ip_address": ip,
            "link_settings": {"speed": 25000, "autoneg": True}}


def config():
    return {"schema": {"name": "system_config", "version": 1},
            "system_info": {"hostname": "t", "network_renderer": "NetworkManager"},
            "nics": [{"mlnx1_p1_25g": nic("192.168.110.1/24")},
                     {"mlnx1_p2_25g": nic("192.168.120.1/24")},
                     {"mlnx2_p1_25g": nic("192.168.150.1/24", role="spare", managed=False)}],
            "cameras": {}}


def device(serial="2012859", mac="e0-55-97-1e-b6-bb", port="mlnx1_p1_25g", ip="192.168.11.1", persistent=True):
    return {"mac": mac, "serial": serial, "model": "HB-2800SC", "ip": ip, "mask": "255.255.255.0",
            "persistent_ip_active": persistent, "dhcp_active": False, "nic": {"name": port, "ip": "192.168.110.1"}}


class PlanTests(unittest.TestCase):
    def test_new_off_subnet_camera_is_programmed_and_added(self):
        plans, problems = ca.plan_adoption([device()], config())
        self.assertEqual(problems, [])
        p, = plans
        self.assertEqual((str(p.target_ip), p.config_action, p.needs_program), ("192.168.110.2", "add", True))
        self.assertEqual(p.config_key, "E0-55-97-1E-B6-BB")
        self.assertEqual(p.entry, {"model": "HB-2800SC", "serial_number": 2012859,
                                   "ip_address": "192.168.110.2", "nic_port": "mlnx1_p1_25g"})

    def test_already_correct_camera_needs_nothing(self):
        cfg = config()
        cfg["cameras"] = {"E0-55-97-1E-B6-BB": {"model": "HB-2800SC", "serial_number": 2012859,
                                                "ip_address": "192.168.110.2", "nic_port": "mlnx1_p1_25g"}}
        plans, problems = ca.plan_adoption([device(ip="192.168.110.2")], cfg)
        self.assertEqual(problems, [])
        self.assertEqual((plans[0].config_action, plans[0].needs_program), ("none", False))

    def test_refusals(self):
        cases = {
            "two cameras on one port": [device(), device(serial="2", mac="e0-55-97-1e-b6-bc")],
            "unknown port": [device(port="enp9s0")],
            "spare port": [device(port="mlnx2_p1_25g")],
        }
        for label, devs in cases.items():
            with self.subTest(label):
                plans, problems = ca.plan_adoption(devs, config())
                self.assertEqual(plans, [])
                self.assertEqual(len(problems), 1, problems)

    def test_known_camera_on_another_port_points_to_move(self):
        cfg = config()
        cfg["cameras"] = {"E0-55-97-1E-B6-BB": {"serial_number": 2012859, "ip_address": "192.168.120.2",
                                                "nic_port": "mlnx1_p2_25g"}}
        plans, problems = ca.plan_adoption([device()], cfg)
        self.assertEqual(plans, [])
        self.assertIn("camera_net_config.py move", problems[0])

    def test_ip_conflict_is_refused(self):
        cfg = config()
        cfg["cameras"] = {"E0-55-97-1E-AA-AA": {"serial_number": 1, "ip_address": "192.168.110.2",
                                                "nic_port": "mlnx1_p1_25g"}}
        plans, problems = ca.plan_adoption([device()], cfg)
        self.assertEqual(plans, [])
        self.assertTrue(problems)


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "config.yml"
        self.path.write_text(yaml.safe_dump(config(), sort_keys=False))
        self.args = argparse.Namespace(evttools=Path("/x/evttools"), evttools_timeout=5, settle_seconds=0,
                                       reboot_check=False, reboot_wait=0, reboot_tool=None)
        self.calls = []
        for target in (patch.object(ca.time, "sleep"), patch.object(ca.os, "geteuid", return_value=0)):
            target.start()
            self.addCleanup(target.stop)

    def run_cmd(self, cmd, timeout):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

    def plans(self):
        return ca.plan_adoption([device()], config())[0]

    def test_programs_with_evttools_verifies_then_writes_config(self):
        after = [device(ip="192.168.110.2")]
        with patch.object(ca.cnc, "run_command", self.run_cmd), patch.object(ca, "discover", return_value=after):
            ca.apply_plans(self.plans(), self.path, yaml.safe_load(self.path.read_text()), self.args)
        self.assertEqual(self.calls, [["/x/evttools", "-f", "192.168.110.1", "-o", "bp"]])
        saved = yaml.safe_load(self.path.read_text())
        self.assertEqual(saved["cameras"]["E0-55-97-1E-B6-BB"]["ip_address"], "192.168.110.2")

    def test_failed_verification_never_writes_config(self):
        before = self.path.read_text()
        still_old = [device()]  # camera did not take the new IP
        with patch.object(ca.cnc, "run_command", self.run_cmd), patch.object(ca, "discover", return_value=still_old):
            with self.assertRaises(ca.AdoptError):
                ca.apply_plans(self.plans(), self.path, yaml.safe_load(before), self.args)
        self.assertEqual(self.path.read_text(), before)

    def test_evttools_failure_never_writes_config(self):
        before = self.path.read_text()
        bad = lambda cmd, timeout: subprocess.CompletedProcess(cmd, 3, "", "boom")
        with patch.object(ca.cnc, "run_command", bad), patch.object(ca, "discover") as disc:
            with self.assertRaises(ca.AdoptError):
                ca.apply_plans(self.plans(), self.path, yaml.safe_load(before), self.args)
        disc.assert_not_called()
        self.assertEqual(self.path.read_text(), before)

    def test_cameras_that_are_already_right_are_left_alone_even_with_reboot_check(self):
        # 2026-10-08 on pancake0: the old loop verified and rebooted every camera, and a failed reboot of an
        # untouched one aborted the adoption of the new one before it was programmed.
        settled = device(serial="2010093", mac="e0-55-97-1e-ab-ed", port="mlnx1_p2_25g", ip="192.168.120.2")
        settled["nic"]["ip"] = "192.168.120.1"
        cfg = config()
        cfg["cameras"] = {"E0-55-97-1E-AB-ED": {"serial_number": 2010093, "model": "HB-2800SC",
                                                "ip_address": "192.168.120.2", "nic_port": "mlnx1_p2_25g"}}
        plans, problems = ca.plan_adoption([settled, device()], cfg)
        self.assertEqual(problems, [])
        self.assertEqual([p.needs_program for p in plans], [False, True])
        self.args.reboot_check = True
        self.args.reboot_tool = "/x/evt_force_reboot"
        after = [device(ip="192.168.110.2")]
        with patch.object(ca.cnc, "run_command", self.run_cmd), patch.object(ca, "discover", return_value=after):
            ca.apply_plans(plans, self.path, cfg, self.args)
        self.assertEqual(self.calls, [["/x/evttools", "-f", "192.168.110.1", "-o", "bp"],
                                      ["/x/evt_force_reboot", "2012859", "192.168.110.2"]])
        self.assertNotIn(["/x/evttools", "-r", "2010093"], self.calls)
        saved = yaml.safe_load(self.path.read_text())
        self.assertEqual(set(saved["cameras"]), {"E0-55-97-1E-AB-ED", "E0-55-97-1E-B6-BB"})

    def test_reboot_check_without_a_reboot_tool_is_skipped_not_fatal(self):
        self.args.reboot_check = True
        after = [device(ip="192.168.110.2")]
        with patch.object(ca.cnc, "run_command", self.run_cmd), patch.object(ca, "discover", return_value=after), \
             patch.dict(ca.os.environ, {"ORANGE_CAMERA_READY_ROOT": str(self.dir)}):
            ca.apply_plans(self.plans(), self.path, yaml.safe_load(self.path.read_text()), self.args)
        self.assertEqual(self.calls, [["/x/evttools", "-f", "192.168.110.1", "-o", "bp"]])
        self.assertIn("E0-55-97-1E-B6-BB", yaml.safe_load(self.path.read_text())["cameras"])

    def test_programming_requires_root(self):
        with patch.object(ca.os, "geteuid", return_value=1000), patch.object(ca.cnc, "run_command") as run:
            with self.assertRaises(ca.AdoptError):
                ca.apply_plans(self.plans(), self.path, config(), self.args)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
