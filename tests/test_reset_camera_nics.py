import subprocess
import unittest
from pathlib import Path

import reset_camera_nics as rcn

CONFIG = {"nics": [
    {"mlnx1_p1_25g": {"role": "camera", "managed": True, "expected_link": True, "pcie_id": "61:00.0", "mac_address": "A0:88:C2:69:11:9E"}},
    {"mlnx1_p2_25g": {"role": "camera", "managed": True, "expected_link": True, "pcie_id": "61:00.1"}},
    {"mlnx1_p3_25g": {"role": "spare", "managed": False, "expected_link": False, "pcie_id": "61:00.2"}},
    {"mlnx2_p3_25g": {"role": "camera", "managed": True, "expected_link": True, "pcie_id": "49:00.2"}},
    {"ethernet_1": {"role": "management", "mac_address": "08:bf:b8:36:af:cc"}},   # no pcie_id: ignored
]}

JOURNAL = """\
2026-10-03T05:56:38-0400 h kernel: mlx5_core 0000:61:00.0: temp_warn:171:(pid 0): High temperature on sensors with bit set 0 8000000000000001
2026-10-03T05:58:06-0400 h kernel: mlx5_core 0000:61:00.0: print_health_info:520:(pid 0): synd 0x10: High temperature
2026-10-03T06:01:36-0400 h kernel: mlx5_core 0000:61:00.1: mlx5_health_try_recover:381:(pid 1): health recovery flow aborted, PCI reads still not working
2026-10-03T06:01:36-0400 h kernel: mlx5_core 0000:61:00.1: E-Switch: Disable: mode(LEGACY)
2026-10-05T13:54:19-0400 h kernel: nvme 0000:2a:00.0: AER: aer_layer=Physical Layer
"""

MLXFWRESET_QUERY = """\
Reset-levels:
0: Driver, PCI link, network link will remain up ("live-Patch")   -Not Supported (Reason: ...)
1: Only ARM side is reset ("Immediate reset")                      -Not Supported
3: Driver restart and PCI reset                                    -Supported
4: Warm Reboot                                                     -Supported
Reset-types (relevant only for reset-levels 3,4):
0: Full chip reset                                                 -Supported
1: Phy-less reset ("port-alive" - network link will remain up)     -Not Supported
Reset-sync (relevant only for reset-level 3):
0: Tool is the owner                                               -Supported
1: Driver is the owner                                             -Supported
"""


class FakeSystem(rcn.System):
    """In-memory sysfs + scripted commands. Mutations are recorded, never executed."""

    def __init__(self, netdevs=None, carriers=None, commands=None, dry_run=False, root=True):
        super().__init__(dry_run=dry_run, log=self.lines.append if hasattr(self, "lines") else print)
        self.lines = []
        self.log = self.lines.append
        self.netdevs = dict(netdevs or {})          # pcie_id -> netdev name present in sysfs
        self.carriers = dict(carriers or {})        # netdev -> "1"/"0"
        self.commands = commands or {}              # argv[0] (or tuple prefix) -> (rc, stdout, stderr) or callable
        self.mutations = []
        self.writes = []
        self.slept = 0.0
        self._clock = 0.0
        self.root = root

    # sysfs
    def _pcie(self, path):
        parts = Path(path).parts
        return parts[parts.index("devices") + 1][5:] if "devices" in parts else None

    def exists(self, path):
        p = str(path)
        if p.startswith("/sys/bus/pci/devices/0000:"):
            pcie = self._pcie(p)
            if p.endswith("/driver"):
                return True
            return pcie in {n["pcie_id"] for n in rcn.mellanox_nics(CONFIG).values()}
        return False

    def listdir(self, path):
        p = str(path)
        if p.endswith("/net"):
            dev = self.netdevs.get(self._pcie(p))
            return [dev] if dev else []
        return []

    def read(self, path):
        p = str(path)
        if p.startswith("/sys/class/net/") and p.endswith("/carrier"):
            return self.carriers.get(p.split("/")[4], "1")
        if p.startswith("/sys/class/net/") and p.endswith("/operstate"):
            return "up"
        if p.startswith("/proc/") and p.endswith("/cmdline"):
            return {"101": "ptp4l\0-i\0mlnx1_p1_25g\0-m", "102": "phc2sys\0-a\0-rr", "555": "/bin/bash\0-c\0pgrep targets/release/orange",
                    "777": "/opt/orange/targets/release/orange\0--config\0x"}.get(p.split("/")[2], "")
        return None

    def realpath(self, path):
        p = str(path)
        if p.endswith("/driver"):
            return "/sys/bus/pci/drivers/mlx5_core"
        if p.endswith("/.."):
            return f"/sys/devices/pci0000:60/0000:60:01.1" if "61:00" in p else "/sys/devices/pci0000:40/0000:40:03.1"
        return p

    # commands
    def run(self, cmd, timeout=120):
        for key, result in self.commands.items():
            prefix = key if isinstance(key, tuple) else (key,)
            if tuple(cmd[:len(prefix)]) == prefix:
                rc, out, err = result(cmd) if callable(result) else result
                return subprocess.CompletedProcess(cmd, rc, out, err)
        if cmd[0] == "journalctl":
            return subprocess.CompletedProcess(cmd, 0, JOURNAL, "")
        if cmd[0] == "pgrep":
            return subprocess.CompletedProcess(cmd, 1, "", "")
        return subprocess.CompletedProcess(cmd, 127, "", f"no fake for {cmd}")

    def mutate(self, cmd, timeout=120):
        self.mutations.append(cmd)
        self.log(f"+ {' '.join(cmd)}")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def mutate_watching(self, cmd, timeout, abort_if=None, interval=10):
        self.mutations.append(cmd)
        self.log(f"+ {' '.join(cmd)}")
        if abort_if and abort_if():
            return subprocess.CompletedProcess(cmd, 124, "", "aborted: the firmware is not booting")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def write(self, path, text):
        self.writes.append((str(path), text))

    def spawn(self, argv, log_path):
        self.mutations.append(["spawn", *argv])

    def sleep(self, seconds):
        self.slept += seconds
        self._clock += seconds

    def now(self):
        return self._clock

    def is_root(self):
        return self.root


HEALTHY = {"61:00.0": "mlnx1_p1_25g", "61:00.1": "mlnx1_p2_25g", "61:00.2": "mlnx1_p3_25g", "49:00.2": "mlnx2_p3_25g"}


class ConfigTests(unittest.TestCase):
    def test_nics_are_grouped_by_card_and_nics_without_pcie_id_are_ignored(self):
        nics = rcn.mellanox_nics(CONFIG)
        self.assertEqual(set(nics), {"mlnx1_p1_25g", "mlnx1_p2_25g", "mlnx1_p3_25g", "mlnx2_p3_25g"})
        self.assertEqual(nics["mlnx1_p1_25g"]["mac"], "a0:88:c2:69:11:9e")
        self.assertEqual(rcn.cards(nics), {
            "49:00": {"49:00.2": "mlnx2_p3_25g"},
            "61:00": {"61:00.0": "mlnx1_p1_25g", "61:00.1": "mlnx1_p2_25g", "61:00.2": "mlnx1_p3_25g"},
        })


class StatusTests(unittest.TestCase):
    def test_all_netdevs_present_is_healthy(self):
        report = rcn.status(FakeSystem(netdevs=HEALTHY), CONFIG)
        self.assertTrue(report["healthy"])
        self.assertEqual([c["card"] for c in report["cards"]], ["49:00", "61:00"])
        self.assertEqual(report["cards"][1]["missing_netdevs"], [])

    def test_missing_netdev_marks_only_that_card_unhealthy_and_collects_its_health_lines(self):
        present = {k: v for k, v in HEALTHY.items() if not k.startswith("61:00")}
        report = rcn.status(FakeSystem(netdevs=present), CONFIG)
        self.assertFalse(report["healthy"])
        by_card = {c["card"]: c for c in report["cards"]}
        self.assertTrue(by_card["49:00"]["healthy"])
        self.assertEqual(by_card["61:00"]["missing_netdevs"], ["mlnx1_p1_25g", "mlnx1_p2_25g", "mlnx1_p3_25g"])
        errors = by_card["61:00"]["health_errors"]
        self.assertEqual(len(errors["61:00.0"]), 2)          # temp_warn + synd line
        self.assertEqual(len(errors["61:00.1"]), 1)          # recovery aborted; E-Switch line is not a health message
        self.assertIn("UNHEALTHY", rcn.format_status(report))

    def test_wrong_netdev_name_is_reported_as_misnamed(self):
        netdevs = dict(HEALTHY, **{"61:00.0": "enp97s0f0np0"})
        report = rcn.status(FakeSystem(netdevs=netdevs), CONFIG)
        card = [c for c in report["cards"] if c["card"] == "61:00"][0]
        self.assertFalse(card["healthy"])
        self.assertEqual(card["misnamed_netdevs"], ["enp97s0f0np0 (expected mlnx1_p1_25g)"])

    def test_orange_detection_needs_the_orange_executable_not_a_shell_mentioning_it(self):
        sys_ = FakeSystem(netdevs=HEALTHY, commands={("pgrep", "-f"): (0, "555\n777\n", "")})
        self.assertEqual(rcn.orange_pids(sys_), [777])

    def test_ptp_processes_come_with_their_argv(self):
        sys_ = FakeSystem(netdevs=HEALTHY, commands={("pgrep", "-x", "ptp4l"): (0, "101\n", ""), ("pgrep", "-x", "phc2sys"): (0, "102\n", "")})
        procs = rcn.ptp_processes(sys_)
        self.assertEqual([(p["name"], p["pid"], p["argv"][0]) for p in procs], [("ptp4l", 101, "ptp4l"), ("phc2sys", 102, "phc2sys")])
        self.assertEqual(rcn.ptp_recipe(procs), ["sudo kill 101 102", "sudo -b ptp4l -i mlnx1_p1_25g -m", "sudo -b phc2sys -a -rr"])


class MlxfwresetTests(unittest.TestCase):
    def test_query_parser_reads_only_the_reset_levels_section(self):
        self.assertEqual(rcn.parse_mlxfwreset_levels(MLXFWRESET_QUERY), {0: False, 1: False, 3: True, 4: True})

    def test_level3_supported_runs_the_reset(self):
        sys_ = FakeSystem(commands={("mlxfwreset", "-d", "61:00.0", "query"): (0, MLXFWRESET_QUERY, "")})
        ok, msg = rcn.reset_card_mlxfwreset(sys_, "61:00")
        self.assertTrue(ok, msg)
        self.assertEqual(sys_.mutations, [["mst", "start"], ["mlxfwreset", "-d", "61:00.0", "--level", "3", "reset", "-y"]])

    def test_query_failure_or_unsupported_level_does_not_reset(self):
        sys_ = FakeSystem(commands={("mlxfwreset",): (1, "", "-E- Failed to open device")})
        ok, msg = rcn.reset_card_mlxfwreset(sys_, "61:00")
        self.assertFalse(ok)
        self.assertIn("query failed", msg)
        sys_ = FakeSystem(commands={("mlxfwreset",): (0, MLXFWRESET_QUERY.replace("PCI reset                                    -Supported", "PCI reset -Not Supported"), "")})
        ok, msg = rcn.reset_card_mlxfwreset(sys_, "61:00")
        self.assertFalse(ok)
        self.assertNotIn(["mlxfwreset", "-d", "61:00.0", "--level", "3", "reset", "-y"], sys_.mutations)


class PciResetTests(unittest.TestCase):
    def test_bus_reset_removes_functions_pulses_sbr_and_rescans(self):
        sys_ = FakeSystem(netdevs={}, commands={("setpci", "-s", "0000:60:01.1", "BRIDGE_CONTROL"): (0, "0012\n", "")})
        functions = rcn.cards(rcn.mellanox_nics(CONFIG))["61:00"]
        ok, msg = rcn.reset_card_pci(sys_, "61:00", functions)
        self.assertTrue(ok, msg)
        self.assertEqual([w[0] for w in sys_.writes], [
            "/sys/bus/pci/devices/0000:61:00.2/remove", "/sys/bus/pci/devices/0000:61:00.1/remove",
            "/sys/bus/pci/devices/0000:61:00.0/remove", "/sys/bus/pci/devices/0000:60:01.1/rescan"])
        self.assertEqual(sys_.mutations, [["setpci", "-s", "0000:60:01.1", "BRIDGE_CONTROL=0052"],
                                          ["setpci", "-s", "0000:60:01.1", "BRIDGE_CONTROL=0012"]])

    def test_unreadable_bridge_control_refuses(self):
        sys_ = FakeSystem(commands={("setpci",): (1, "", "setpci: Permission denied")})
        ok, msg = rcn.reset_card_pci(sys_, "61:00", {"61:00.0": "mlnx1_p1_25g"})
        self.assertFalse(ok)
        self.assertEqual(sys_.writes, [])


class ResetFlowTests(unittest.TestCase):
    def _args(self, **kw):
        base = dict(card=None, method="auto", wait=10, restart_ptp=False, force=False, dry_run=False)
        base.update(kw)
        return type("Args", (), base)()

    def test_refuses_while_orange_is_running_unless_forced(self):
        sys_ = FakeSystem(netdevs={}, commands={("pgrep", "-f"): (0, "777\n", "")})
        report = rcn.status(sys_, CONFIG)
        self.assertEqual(rcn.do_reset(sys_, CONFIG, report, self._args(), sys_.log), 2)
        self.assertEqual(sys_.mutations, [])

    def test_refuses_without_root(self):
        sys_ = FakeSystem(netdevs={}, root=False)
        self.assertEqual(rcn.do_reset(sys_, CONFIG, rcn.status(sys_, CONFIG), self._args(), sys_.log), 2)

    def test_healthy_cards_are_left_alone(self):
        sys_ = FakeSystem(netdevs=HEALTHY)
        self.assertEqual(rcn.do_reset(sys_, CONFIG, rcn.status(sys_, CONFIG), self._args(), sys_.log), 0)
        self.assertEqual(sys_.mutations, [])

    def test_unhealthy_card_falls_back_to_pci_reset_and_nudges_nm(self):
        present = {k: v for k, v in HEALTHY.items() if not k.startswith("61:00")}
        calls = {"n": 0}

        def nm_state(cmd):
            return (0, "100 (connected)\n" if cmd[-1] == "mlnx1_p1_25g" else "30 (disconnected)\n", "")

        sys_ = FakeSystem(netdevs=present, commands={
            ("mlxfwreset",): (1, "", "-E- Failed to open device"),
            ("setpci", "-s", "0000:60:01.1", "BRIDGE_CONTROL"): (0, "0012\n", ""),
            ("nmcli", "-g", "GENERAL.STATE"): nm_state,
        })
        report = rcn.status(sys_, CONFIG)
        # the card "comes back" once the rescan has been written
        original_write = sys_.write

        def write_and_recover(path, text):
            original_write(path, text)
            if str(path).endswith("/rescan"):
                sys_.netdevs.update({k: v for k, v in HEALTHY.items() if k.startswith("61:00")})
        sys_.write = write_and_recover

        rc = rcn.do_reset(sys_, CONFIG, report, self._args(), sys_.log)
        self.assertEqual(rc, 0, "\n".join(sys_.lines))
        self.assertIn(["setpci", "-s", "0000:60:01.1", "BRIDGE_CONTROL=0052"], sys_.mutations)
        self.assertNotIn(["setpci", "-s", "0000:40:03.1", "BRIDGE_CONTROL=0052"], sys_.mutations)   # healthy card untouched
        self.assertIn(["nmcli", "connection", "up", "mlnx1_p2_25g"], sys_.mutations)                # managed, disconnected
        self.assertNotIn(["nmcli", "connection", "up", "mlnx1_p1_25g"], sys_.mutations)             # already connected
        self.assertNotIn(["nmcli", "connection", "up", "mlnx1_p3_25g"], sys_.mutations)             # spare, unmanaged

    def test_restart_ptp_kills_then_respawns_ptp4l_before_phc2sys(self):
        sys_ = FakeSystem(netdevs=HEALTHY, commands={("pgrep", "-x", "ptp4l"): (0, "101\n", ""), ("pgrep", "-x", "phc2sys"): (0, "102\n", "")})
        report = rcn.status(sys_, CONFIG)
        rcn.do_reset(sys_, CONFIG, report, self._args(card=["61:00"], method="mlxfwreset", restart_ptp=True), sys_.log)
        # mlxfwreset is unavailable in this fake, so the reset fails, but the PTP handling still runs
        spawned = [m for m in sys_.mutations if m[0] == "spawn"]
        self.assertEqual([m[1] for m in spawned], ["ptp4l", "phc2sys"])
        self.assertIn(["kill", "101"], sys_.mutations)


if __name__ == "__main__":
    unittest.main()


DEAD_FW_JOURNAL = JOURNAL + """\
2026-10-05T14:27:20-0400 h kernel: mlx5_core 0000:61:00.0: mlx5_function_enable:1479:(pid 1): Firmware over 120000 MS in pre-initializing state, aborting
2026-10-05T14:27:20-0400 h kernel: mlx5_core 0000:61:00.0: firmware version: 65535.65535.65535
"""


class RealSystemTests(unittest.TestCase):
    def test_timeouts_return_text_not_bytes(self):
        res = rcn.System().run(["sh", "-c", "echo partial; sleep 5"], timeout=0.3)
        self.assertEqual((res.returncode, res.stdout), (124, "partial\n"))
        self.assertIn("timed out", res.stderr)

    def test_mutate_watching_kills_the_command_when_asked(self):
        sys_ = rcn.System(log=lambda *_: None)
        res = sys_.mutate_watching(["sh", "-c", "echo go; sleep 30"], timeout=20, abort_if=lambda: True, interval=1)
        self.assertEqual(res.returncode, 124)
        self.assertIn("aborted", res.stderr)
        self.assertEqual(res.stdout, "go\n")
        res = sys_.mutate_watching(["sh", "-c", "echo done"], timeout=5, abort_if=lambda: True, interval=1)
        self.assertEqual((res.returncode, res.stdout), (0, "done\n"))


class DeadFirmwareTests(unittest.TestCase):
    def test_firmware_dead_lines_are_found_for_the_card_only(self):
        sys_ = FakeSystem(commands={("journalctl", "-k", "-o", "short-iso", "--no-pager", "--since"): (0, DEAD_FW_JOURNAL, "")})
        self.assertEqual(len(rcn.firmware_dead(sys_, ["61:00.0", "61:00.1"], "2026-10-05T14:25:00")), 2)
        self.assertEqual(rcn.firmware_dead(sys_, ["49:00.2"], "2026-10-05T14:25:00"), [])

    def test_reset_stops_after_the_first_method_when_the_firmware_never_boots(self):
        present = {k: v for k, v in HEALTHY.items() if not k.startswith("61:00")}
        sys_ = FakeSystem(netdevs=present, commands={
            ("mlxfwreset", "-d", "61:00.0", "query"): (0, MLXFWRESET_QUERY, ""),
            ("journalctl", "-k", "-o", "short-iso", "--no-pager", "--since"): (0, DEAD_FW_JOURNAL, ""),
            ("setpci",): (0, "0012\n", ""),
        })
        args = type("Args", (), dict(card=None, method="auto", wait=10, restart_ptp=False, force=False, dry_run=False))()
        rc = rcn.do_reset(sys_, CONFIG, rcn.status(sys_, CONFIG), args, sys_.log)
        self.assertEqual(rc, 2)
        self.assertIn(["mlxfwreset", "-d", "61:00.0", "--level", "3", "reset", "-y"], sys_.mutations)
        self.assertTrue(any("aborted: the firmware is not booting" in l for l in sys_.lines), sys_.lines[-6:])
        self.assertEqual([m for m in sys_.mutations if m[0] == "setpci"], [])          # PCI route skipped
        self.assertTrue(any("power the host off" in l for l in sys_.lines), sys_.lines[-5:])
