import os
import unittest
import unittest.mock
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

    def _ptp(self, journal):
        host = FakeHost(netdevs=HEALTHY, commands={
            ("pgrep", "-x", "ptp4l"): (0, "101\n", ""), ("pgrep", "-x", "phc2sys"): (0, "102\n", ""),
            ("journalctl", "_COMM=phc2sys"): (0, journal, "")})
        return rhc.check_ptp(host, CONFIG, rhc.THRESHOLDS)

    def test_ptp_offsets(self):
        # a port stuck unlocked with a garbage offset (the 2026-10-03 state) is critical
        stuck = "mlnx1_p1_25g sys offset -201442512784858 s0 freq -2319 delay 661\nmlnx1_p2_25g sys offset 12 s2 freq +4 delay 600\n"
        findings = self._ptp(stuck)
        self.assertEqual([f.level for f in findings], ["crit"])
        self.assertIn("not locked on mlnx1_p1_25g", findings[0].message)
        # the step at start-up is not a fault once every port has locked
        startup = ("mlnx1_p1_25g sys offset -525329394204 s0 freq -19864 delay 661\n"
                   "mlnx1_p1_25g sys offset -525329394100 s1 freq -19864 delay 661\n"
                   "mlnx1_p1_25g sys offset 109 s2 freq -19864 delay 661\n"
                   "phc2sys[2361.455]: mlnx2_p3_25g sys offset 114 s2 freq -8079 delay 681\n")
        findings = self._ptp(startup)
        self.assertEqual([(f.level, f.value) for f in findings], [("ok", 114)])
        # a locked port that wanders past the threshold still warns
        self.assertEqual([f.level for f in self._ptp("mlnx1_p1_25g sys offset 250000 s2 freq 1 delay 600\n")], ["warn"])
        host = FakeHost(netdevs=HEALTHY, commands={("pgrep", "-x", "ptp4l"): (1, "", ""), ("pgrep", "-x", "phc2sys"): (1, "", "")})
        self.assertEqual(sorted(f.message for f in rhc.check_ptp(host, CONFIG, rhc.THRESHOLDS)), ["phc2sys is not running", "ptp4l is not running"])

    def test_unreadable_kernel_log_is_a_warning_not_a_silent_ok(self):
        host = FakeHost(netdevs=HEALTHY, commands={("journalctl", "-k"): (1, "", "Failed to parse timestamp: x")})
        self.assertEqual([f.level for f in rhc.check_mlx5_health(host, CONFIG, "x")], ["warn"])
        self.assertEqual([f.level for f in rhc.check_pcie_aer(host, "x")], ["warn"])

    def test_aer_and_mlx5_health_use_the_journal_window(self):
        host = FakeHost(netdevs=HEALTHY)
        aer = rhc.check_pcie_aer(host, "-15min")[0]
        self.assertEqual((aer.level, aer.value), ("warn", 1))
        self.assertIn("nvme 0000:2a:00.0", aer.message)
        health = rhc.check_mlx5_health(host, CONFIG, "-15min")[0]
        self.assertEqual((health.level, health.value), ("crit", 3))


class MailTests(unittest.TestCase):
    def test_uses_the_system_msmtp_config_and_builds_a_proper_message(self):
        class H(FakeHost):
            def exists(self, path):
                return str(path) == "/etc/msmtprc" or super().exists(path)
        host = H(commands={("msmtp",): (0, "", "")})
        with unittest.mock.patch.dict(os.environ, {"RIG_HEALTH_MSMTP_CONFIG": ""}):
            outcome = rhc.send_mail(host, "me@example.org", "rig-health@host", "[rig-health] host: x", "body")
        self.assertEqual(outcome, "mailed me@example.org via msmtp -C /etc/msmtprc")
        self.assertTrue(host.last_input.startswith("To: me@example.org\nFrom: rig-health@host\nSubject: [rig-health] host: x\n\nbody"))

    def test_falls_back_to_sendmail_and_reports_failures(self):
        host = FakeHost(commands={("msmtp",): (127, "", "not found"), ("sendmail",): (1, "", "relay refused")})
        with unittest.mock.patch.dict(os.environ, {"RIG_HEALTH_MSMTP_CONFIG": ""}):
            self.assertIn("sendmail failed rc 1", rhc.send_mail(host, "a@b", "c@d", "s", "b"))
        host = FakeHost(commands={("msmtp",): (127, "", ""), ("sendmail",): (127, "", "")})
        with unittest.mock.patch.dict(os.environ, {"RIG_HEALTH_MSMTP_CONFIG": ""}):
            self.assertEqual(rhc.send_mail(host, "a@b", "c@d", "s", "b"), "no msmtp/sendmail available")


class TimeSyncTests(unittest.TestCase):
    def _host(self, ntp="yes", synced="yes", offset=0.002, fail=False):
        class H(FakeHost):
            def sntp_offset(self, server, timeout=3.0):
                if fail:
                    raise OSError("timed out")
                return offset
        return H(commands={("timedatectl",): (0, f"NTP={ntp}\nNTPSynchronized={synced}\n", "")})

    def test_synchronized_and_close_is_ok(self):
        self.assertEqual([(f.level, f.check) for f in rhc.check_time_sync(self._host(), rhc.THRESHOLDS, "pool")], [("ok", "time_sync")])

    def test_no_ntp_service_and_eight_minutes_behind_is_crit(self):
        findings = rhc.check_time_sync(self._host(ntp="no", synced="no", offset=478.7), rhc.THRESHOLDS, "pool")
        self.assertEqual(sorted(f.level for f in findings), ["crit", "warn"])
        self.assertTrue(any("478.700 s behind" in f.message for f in findings))
        self.assertTrue(any("set-ntp true" in f.message for f in findings))

    def test_query_failure_is_only_informational_and_query_can_be_skipped(self):
        findings = rhc.check_time_sync(self._host(fail=True), rhc.THRESHOLDS, "pool")
        self.assertEqual([f.level for f in findings], ["info"])
        self.assertEqual([f.level for f in rhc.check_time_sync(self._host(ntp="yes", synced="no"), rhc.THRESHOLDS, None)], ["warn"])


RESULT = {"host": "pancake0", "time": "2026-10-06T17:18:04", "worst": "crit", "mail": "mailed me@x via msmtp",
          "findings": [{"check": "camera_nics", "level": "ok", "message": "8 netdevs present", "value": None},
                       {"check": "time_sync", "level": "crit", "message": "system clock is 488.266 s behind pool", "value": 488.266},
                       {"check": "ptp", "level": "warn", "message": "ptp4l is not running [x]", "value": None}],
          "transitions": {"time_sync": ["ok", "crit"]}}


class RenderTests(unittest.TestCase):
    def _render(self, color, rich_mods="auto"):
        import io
        buf = io.StringIO()
        used = rhc.render(RESULT, color, stream=buf, rich_mods=rich_mods)
        return used, buf.getvalue()

    def test_plain_output_has_no_escape_codes_and_matches_format_findings(self):
        used, out = self._render(False)
        self.assertEqual(used, "plain")
        self.assertNotIn("\033", out)
        self.assertEqual(out.rstrip("\n"), rhc.format_findings(RESULT))

    def test_ansi_fallback_colours_levels_when_rich_is_missing(self):
        used, out = self._render(True, rich_mods=None)
        self.assertEqual(used, "ansi")
        self.assertIn("\033[1;31m[crit]", out)
        self.assertIn("\033[33m[warn]", out)
        self.assertIn("\033[32m[ok  ]", out)
        self.assertIn("488.266 s behind", out)

    @unittest.skipUnless(rhc.load_rich(), "rich not installed for this interpreter")
    def test_rich_renderer_keeps_every_message_and_literal_brackets(self):
        used, out = self._render(True)
        self.assertEqual(used, "rich")
        self.assertIn("\033[", out)
        for text in ("8 netdevs present", "488.266 s behind", "not running [x]", "time_sync", "CRIT"):
            self.assertIn(text, out)

    def test_auto_colour_only_on_a_terminal_and_respects_no_color(self):
        class Tty:
            def isatty(self): return True
        class Pipe:
            def isatty(self): return False
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NO_COLOR", None)
            self.assertTrue(rhc.want_color("auto", Tty()))
            self.assertFalse(rhc.want_color("auto", Pipe()))
            os.environ["NO_COLOR"] = "1"
            self.assertFalse(rhc.want_color("auto", Tty()))
            self.assertTrue(rhc.want_color("always", Pipe()))
            self.assertFalse(rhc.want_color("never", Tty()))


class StateTests(unittest.TestCase):
    def test_levels_by_check_keeps_the_worst_and_transitions_ignore_info(self):
        findings = [rhc.Finding("a", "ok", ""), rhc.Finding("a", "warn", ""), rhc.Finding("b", "info", ""), rhc.Finding("c", "crit", "")]
        levels = rhc.levels_by_check(findings)
        self.assertEqual(levels, {"a": "warn", "b": "info", "c": "crit"})
        self.assertEqual(rhc.worst_level(findings), "crit")
        self.assertEqual(rhc.transitions({"a": "ok", "b": "ok", "c": "crit", "d": "warn"}, levels), {"a": ("ok", "warn"), "d": ("warn", "ok")})


if __name__ == "__main__":
    unittest.main()
