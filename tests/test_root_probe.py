import ast
import importlib.util
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import capture_inventory as ci

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "libexec" / "pancakebatter_root_probe.py"
spec = importlib.util.spec_from_file_location("root_probe", PROBE)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def fake_sysfs(tmp, vpd=b"\x82\x00\x00\x78"):
    net = Path(tmp) / "net"
    for name, physical in (("enp1", True), ("lo", False), ("wlan0", True)):
        (net / name).mkdir(parents=True)
        if physical:
            (net / name / "device").mkdir()
    (net / "enp1" / "device" / "vpd").write_bytes(vpd)
    (net / "wlan0" / "wireless").mkdir()
    return net


class ProbeTests(unittest.TestCase):
    def test_collects_only_physical_wired_nics_with_fixed_command(self):
        calls = []

        def fake_run(args, **kw):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout="Vendor name : X\n")

        with tempfile.TemporaryDirectory() as tmp:
            out = probe.collect(sys_net=str(fake_sysfs(tmp)), ethtool="/usr/sbin/ethtool", run=fake_run)
        self.assertEqual(list(out), ["enp1"])
        self.assertEqual(calls, [["/usr/sbin/ethtool", "-m", "enp1"]])
        calls.clear()
        self.assertEqual(probe.collect_slots(dmidecode="/usr/sbin/dmidecode", run=fake_run), "Vendor name : X\n")
        self.assertEqual(calls, [["/usr/sbin/dmidecode", "-t", "slot"]])
        self.assertEqual(out["enp1"]["ethtool_m"], "Vendor name : X\n")
        self.assertEqual(out["enp1"]["vpd_hex"], "82000078")

    def test_failures_become_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            net = fake_sysfs(tmp)
            (net / "enp1" / "device" / "vpd").unlink()
            out = probe.collect(sys_net=str(net), run=lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
        self.assertEqual(out["enp1"], {"ethtool_m": None, "vpd_hex": None})

    def test_rejects_odd_interface_names(self):
        self.assertFalse(probe.IFACE_RE.match("eth0; rm -rf /"))
        self.assertFalse(probe.IFACE_RE.match("a" * 16))
        self.assertFalse(probe.IFACE_RE.match("eth0 -s"))
        self.assertTrue(probe.IFACE_RE.match("enp241s0f0np0"))

    def test_source_stays_read_only(self):
        """Guard: no writes, no shells, and only the one ethtool invocation."""
        src = PROBE.read_text()
        code = re.sub(r'""".*?"""', "", src, flags=re.S)
        for forbidden in ("shell=True", "os.system", "os.popen", "os.remove", "os.unlink", "os.chmod",
                          "os.rename", "shutil", "Popen", "sys.argv", "input(", "environ"):
            self.assertNotIn(forbidden, code, forbidden)
        opens = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "open"]
        self.assertTrue(opens)
        for call in opens:
            self.assertEqual(ast.literal_eval(call.args[1]), "rb")
        self.assertEqual(re.findall(r'\[ethtool, "([^"]+)"', code), ["-m"])
        self.assertEqual(re.findall(r'\[dmidecode, "([^"]+)", "([^"]+)"\]', code), [("-t", "slot")])
        # exactly two subprocess invocations exist, and nothing but `run` launches processes
        self.assertEqual(len(re.findall(r"\brun\(\[", code)), 2)


class CaptureIntegrationTests(unittest.TestCase):
    def setUp(self):
        ci.load_privileged.cache_clear()
        self.addCleanup(ci.load_privileged.cache_clear)

    def test_no_helper_means_no_sudo_call(self):
        with patch.object(ci, "ROOT_PROBE", "/nonexistent/probe"), patch.object(ci, "run") as run:
            self.assertEqual(ci.load_privileged(), {})
        run.assert_not_called()

    def test_helper_json_is_used_with_non_interactive_sudo(self):
        with tempfile.NamedTemporaryFile() as helper, \
             patch.object(ci, "ROOT_PROBE", helper.name), \
             patch.object(ci.os, "geteuid", return_value=1000), \
             patch.object(ci, "run", return_value='{"nics": {"enp1": {"ethtool_m": "x", "vpd_hex": null}}, "dmi_slots": "s"}') as run:
            got = ci.load_privileged()
        self.assertEqual(got["nics"]["enp1"]["ethtool_m"], "x")
        self.assertEqual(got["dmi_slots"], "s")
        self.assertEqual(run.call_args[0][0][:2], ["sudo", "-n"])

    def test_bad_helper_output_is_ignored(self):
        with tempfile.NamedTemporaryFile() as helper, \
             patch.object(ci, "ROOT_PROBE", helper.name), \
             patch.object(ci.os, "geteuid", return_value=1000), \
             patch.object(ci, "run", return_value="not json"):
            self.assertEqual(ci.load_privileged(), {})


if __name__ == "__main__":
    unittest.main()
