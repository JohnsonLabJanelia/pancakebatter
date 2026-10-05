import os
import unittest
from collections import namedtuple

import rig_health_check as rhc
import reset_camera_nics as rcn
try:
    from tests.test_reset_camera_nics import CONFIG, FakeSystem, HEALTHY
except ImportError:  # discover -s tests puts tests/ itself on sys.path
    from test_reset_camera_nics import CONFIG, FakeSystem, HEALTHY

Statvfs = namedtuple("Statvfs", "f_blocks f_bavail f_frsize")


class FakeHost(FakeSystem):
    def __init__(self, hwmon=None, disks=None, **kw):
        super().__init__(**kw)
        self.hwmon = hwmon or {}      # hwmonN -> {"name":..., "device": "0000:61:00.0", "temps": {"1": {"label","input","max","crit"}}}
        self.disks = disks or {"/": Statvfs(1000, 500, 4096)}

    def listdir(self, path):
        p = str(path)
        if p == str(rhc.HWMON):
            return sorted(self.hwmon)
        if p.startswith(str(rhc.HWMON) + "/"):
            entry = self.hwmon[p.split("/")[4]]
            attrs = ["name"]
            for idx, t in entry["temps"].items():
                attrs += [f"temp{idx}_{k}" for k in t]
            return sorted(attrs)
        return super().listdir(path)

    def exists(self, path):
        p = str(path)
        if p.startswith(str(rhc.HWMON) + "/"):
            return p.endswith("/device") and bool(self.hwmon[p.split("/")[4]].get("device"))
        return super().exists(path)

    def read(self, path):
        p = str(path)
        if p.startswith(str(rhc.HWMON) + "/"):
            parts = p.split("/")
            entry, attr = self.hwmon[parts[4]], parts[5]
            if attr == "name":
                return entry["name"]
            idx, key = attr[4:].split("_", 1)
            value = entry["temps"][idx].get(key)
            return None if value is None else (str(value) if key == "label" else str(int(value * 1000)))
        return super().read(path)

    def realpath(self, path):
        p = str(path)
        if p.startswith(str(rhc.HWMON) + "/") and p.endswith("/device"):
            return "/sys/devices/pci0000:60/" + self.hwmon[p.split("/")[4]]["device"]
        return super().realpath(path)

    def statvfs(self, path):
        if path not in self.disks:
            raise OSError(2, "No such file or directory")
        return self.disks[path]


def mlx5(device, asic, module=None):
    temps = {"1": {"label": "asic", "input": asic, "crit": 105}}
    if module is not None:
        temps["2"] = {"label": "Module0", "input": module, "crit": 75}
    return {"name": "mlx5", "device": device, "temps": temps}


class CpuListTests(unittest.TestCase):
    def test_parse_and_format_round_trip(self):
        self.assertEqual(rhc.parse_cpulist("1,2,6-8,10"), {1, 2, 6, 7, 8, 10})
        self.assertEqual(rhc.format_cpulist([0, 3, 4, 5, 7, 9, 10, 11]), "0 3-5 7 9-11")

    def test_allowed_cpus_excludes_isolated_cores(self):
        config = {"kernel_tuning": {"isolated_cores": "1,2,6"}}
        self.assertEqual(rhc.allowed_cpus(config, 8), [0, 3, 4, 5, 7])
        self.assertEqual(rhc.allowed_cpus({}, 4), [0, 1, 2, 3])
        self.assertEqual(rhc.allowed_cpus({"kernel_tuning": {"isolated_cores": "0-3"}}, 4), [0, 1, 2, 3])  # never an empty set


class CameraNicTests(unittest.TestCase):
    def test_present_with_carrier_is_ok(self):
        findings = rhc.check_camera_nics(FakeHost(netdevs=HEALTHY), CONFIG)
        self.assertEqual([(f.level, f.check) for f in findings], [("ok", "camera_nics")])

    def test_missing_card_is_crit_and_no_carrier_on_camera_port_is_warn(self):
        present = {k: v for k, v in HEALTHY.items() if k.startswith("61:00")}
        host = FakeHost(netdevs=present, carriers={"mlnx1_p2_25g": "0", "mlnx1_p3_25g": "0"})
        findings = rhc.check_camera_nics(host, CONFIG)
        levels = {(f.level, f.message.split(":")[0]) for f in findings}
        self.assertIn(("crit", "card 49"), levels)
        self.assertIn(("warn", "mlnx1_p2_25g"), levels)
        self.assertNotIn(("warn", "mlnx1_p3_25g"), levels)   # spare: expected_link false


class TemperatureTests(unittest.TestCase):
    def test_nic_thresholds_and_dead_sensors(self):
        host = FakeHost(netdevs=HEALTHY, hwmon={
            "hwmon3": mlx5("0000:61:00.0", 62, 50),
            "hwmon4": mlx5("0000:61:00.1", 88),
            "hwmon5": mlx5("0000:49:00.2", 0, 0),
            "hwmon6": {"name": "k10temp", "device": "", "temps": {"1": {"label": "Tctl", "input": 54}}},
            "hwmon7": {"name": "nvme", "device": "0000:2a:00.0", "temps": {"1": {"label": "Composite", "input": 47, "max": 84, "crit": 88}}},
        })
        findings = rhc.check_temperatures(host, CONFIG, rhc.THRESHOLDS, with_gpu=False)
        by = {(f.check, f.level): f.message for f in findings}
        self.assertIn(("nic_temps", "warn"), by)
        messages = [f.message for f in findings if f.check == "nic_temps"]
        self.assertTrue(any("mlnx1_p2_25g asic: 88 C" in m for m in messages), messages)
        self.assertTrue(any(m.startswith("card 49:00: 2 sensors read 0 C") for m in messages), messages)
        self.assertFalse(any(f.level == "ok" for f in findings if f.check == "nic_temps"))   # ok readings are dropped once a check has warnings
        self.assertEqual([f.level for f in findings if f.check == "cpu_temp"], ["ok"])
        self.assertEqual([f.level for f in findings if f.check == "nvme_temps"], ["ok"])

    def test_nvme_uses_its_own_max_and_crit(self):
        host = FakeHost(netdevs=HEALTHY, hwmon={
            "hwmon0": mlx5("0000:61:00.0", 60),
            "hwmon7": {"name": "nvme", "device": "0000:2a:00.0", "temps": {"1": {"label": "Composite", "input": 86, "max": 84, "crit": 88}}},
        })
        findings = rhc.check_temperatures(host, CONFIG, rhc.THRESHOLDS, with_gpu=False)
        self.assertEqual([f.level for f in findings if f.check == "nvme_temps"], ["warn"])

    def test_gpu_parsing(self):
        host = FakeHost(commands={("nvidia-smi",): (0, "0, NVIDIA RTX A6000, 54\n1, NVIDIA A16, 93\n", "")})
        findings = rhc.check_gpus(host, rhc.THRESHOLDS)
        self.assertEqual([f.level for f in findings], ["ok", "crit"])
        host = FakeHost(commands={("nvidia-smi",): (9, "", "NVIDIA-SMI has failed")})
        self.assertEqual(rhc.check_gpus(host, rhc.THRESHOLDS)[0].level, "warn")


class DiskPtpAerTests(unittest.TestCase):
    def test_disk_space_levels_and_data_mounts_from_config(self):
        config = dict(CONFIG, storage_devices=[{"partitions": [{"mount_point": "/mnt/Data1"}, {"mount_point": "/boot/efi"}]}])
        host = FakeHost(disks={"/": Statvfs(1000, 500, 4096), "/mnt/Data1": Statvfs(1000, 20, 4096)})
        findings = rhc.check_disk_space(host, config, rhc.THRESHOLDS)
        self.assertEqual({f.message.split(":")[0]: f.level for f in findings}, {"/mnt/Data1": "crit"})   # ok mounts drop out once one is not
        host = FakeHost(disks={"/": Statvfs(1000, 500, 4096), "/mnt/Data1": Statvfs(1000, 500, 4096)})
        findings = rhc.check_disk_space(host, config, rhc.THRESHOLDS)
        self.assertEqual([(f.check, f.level) for f in findings], [("disk_space", "ok")])

    def test_ptp_offsets(self):
        offsets = "mlnx1_p1_25g sys offset -201442512784858 s0 freq -2319 delay 661\nmlnx1_p2_25g sys offset 12 s2 freq +4 delay 600\n"
        host = FakeHost(netdevs=HEALTHY, commands={
            ("pgrep", "-x", "ptp4l"): (0, "101\n", ""), ("pgrep", "-x", "phc2sys"): (0, "102\n", ""),
            ("journalctl", "_COMM=phc2sys"): (0, offsets, "")})
        findings = rhc.check_ptp(host, CONFIG, rhc.THRESHOLDS)
        self.assertEqual([f.level for f in findings], ["crit"])
        host = FakeHost(netdevs=HEALTHY, commands={("pgrep", "-x", "ptp4l"): (1, "", ""), ("pgrep", "-x", "phc2sys"): (1, "", "")})
        self.assertEqual(sorted(f.message for f in rhc.check_ptp(host, CONFIG, rhc.THRESHOLDS)), ["phc2sys is not running", "ptp4l is not running"])

    def test_aer_and_mlx5_health_use_the_journal_window(self):
        host = FakeHost(netdevs=HEALTHY)
        aer = rhc.check_pcie_aer(host, "-15min")[0]
        self.assertEqual((aer.level, aer.value), ("warn", 1))
        self.assertIn("nvme 0000:2a:00.0", aer.message)
        health = rhc.check_mlx5_health(host, CONFIG, "-15min")[0]
        self.assertEqual((health.level, health.value), ("crit", 3))


class StateTests(unittest.TestCase):
    def test_levels_by_check_keeps_the_worst_and_transitions_ignore_info(self):
        findings = [rhc.Finding("a", "ok", ""), rhc.Finding("a", "warn", ""), rhc.Finding("b", "info", ""), rhc.Finding("c", "crit", "")]
        levels = rhc.levels_by_check(findings)
        self.assertEqual(levels, {"a": "warn", "b": "info", "c": "crit"})
        self.assertEqual(rhc.worst_level(findings), "crit")
        self.assertEqual(rhc.transitions({"a": "ok", "b": "ok", "c": "crit", "d": "warn"}, levels), {"a": ("ok", "warn"), "d": ("warn", "ok")})


if __name__ == "__main__":
    unittest.main()
