import ast
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import rig_health_check as rhc
try:
    from tests.test_reset_camera_nics import CONFIG
    from tests.test_rig_health_check import FakeHost, Statvfs
except ImportError:  # discover -s tests puts tests/ itself on sys.path
    from test_reset_camera_nics import CONFIG
    from test_rig_health_check import FakeHost, Statvfs

ROOT = Path(__file__).resolve().parent.parent
SNAP = ROOT / "libexec" / "nvme_smart_snapshot.py"
spec = importlib.util.spec_from_file_location("nvme_smart_snapshot", SNAP)
snap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snap)

# Lines as the kernel logged them on pancake0 at 2026-10-16 00:03 (Data1 failing).
FAILING = (
    "2026-10-16T00:03:45-0400 pancake0 kernel: nvme1n1: I/O Cmd(0x2) @ LBA 896469728, 256 blocks, I/O Error (sct 0x2 / sc 0x81) MORE \n"
    "2026-10-16T00:03:45-0400 pancake0 kernel: critical medium error, dev nvme1n1, sector 896469728 op 0x0:(READ) flags 0x80700 phys_seg 32 prio class 2\n"
    "2026-10-16T00:03:47-0400 pancake0 kernel: critical medium error, dev nvme1n1, sector 896469904 op 0x0:(READ) flags 0x0 phys_seg 1 prio class 2\n"
    "2026-10-16T00:03:50-0400 pancake0 kernel: nvme nvme1: I/O 12 QID 3 timeout, aborting\n"
    "2026-10-16T00:03:51-0400 pancake0 kernel: nvme nvme1: resetting controller\n"
    "2026-10-16T00:04:00-0400 pancake0 kernel: mlx5_core 0000:61:00.0: something unrelated\n"
)
DRIVES = {   # what /sys/class/nvme showed on pancake0
    "nvme0": {"model": "Sabrent Rocket 4 Plus", "serial": "48816072200219", "firmware_rev": "R4PTLC.4", "namespaces": ["nvme0n1"]},
    "nvme1": {"model": "Sabrent Rocket 4 Plus", "serial": "48816072200111", "firmware_rev": "R4PTLC.3", "namespaces": ["nvme1n1"]},
    "nvme2": {"model": "Samsung SSD 990 PRO 4TB", "serial": "S7KGNJ0X", "firmware_rev": "4B2QJXD7", "namespaces": ["nvme2n1"]},
}


def smart(**over):
    base = {"critical_warning": 0, "temperature": 330, "avail_spare": 100, "spare_thresh": 10, "percent_used": 0,
            "unsafe_shutdowns": 10, "media_errors": 0, "num_err_log_entries": 5, "warning_temp_time": 0, "critical_comp_time": 0}
    base.update(over)
    return base


def snapshot(time=None, **per_ctrl):
    doc = {"schema": snap.SCHEMA, "time": (time or datetime.now().astimezone()).isoformat(timespec="seconds"), "controllers": {}}
    for ctrl, d in DRIVES.items():
        doc["controllers"][ctrl] = {**{k: d[k] for k in ("model", "serial", "firmware_rev", "namespaces")},
                                    "smart": per_ctrl.get(ctrl, smart()), "error": None}
    return doc


class StorageHost(FakeHost):
    def __init__(self, journal="", snap_doc=None, drives=None, devs=None, **kw):
        super().__init__(commands={("journalctl", "-k"): (0, journal, "")}, **kw)
        self.drives = DRIVES if drives is None else drives
        self.snap_doc = snap_doc
        self.devs = devs or {}       # path -> (major, minor, sysfs real path of the block device)

    def listdir(self, path):
        p = str(path)
        if p == str(rhc.SYS_NVME):
            return sorted(self.drives)
        if p.startswith(str(rhc.SYS_NVME) + "/"):
            d = self.drives[p.split("/")[4]]
            return sorted(["model", "serial", "firmware_rev", "device", *d["namespaces"], "ng" + d["namespaces"][0][4:]])
        return super().listdir(path)

    def read(self, path):
        p = str(path)
        if p.startswith(str(rhc.SYS_NVME) + "/"):
            _, _, _, _, ctrl, attr = p.split("/")
            return self.drives[ctrl].get(attr)
        if p == str(rhc.SMART_SNAPSHOT):
            return None if self.snap_doc is None else (self.snap_doc if isinstance(self.snap_doc, str) else json.dumps(self.snap_doc))
        return super().read(path)

    def stat_dev(self, path):
        if path not in self.devs:
            raise FileNotFoundError(2, "No such file or directory")
        return self.devs[path][:2]

    def realpath(self, path):
        p = str(path)
        for major, minor, real in self.devs.values():
            if p == f"/sys/dev/block/{major}:{minor}":
                return real
        return super().realpath(path)


TH = dict(rhc.THRESHOLDS)


class StorageErrorTests(unittest.TestCase):
    def test_counts_per_controller_and_names_the_drive(self):
        counts = rhc.storage_error_counts(FAILING.splitlines())
        self.assertEqual(counts, {"nvme1": {"nvme I/O error": 1, "critical medium error": 2, "nvme timeout": 1, "nvme reset": 1}})
        self.assertEqual(rhc.controller_of("nvme1n1p1"), "nvme1")
        self.assertEqual(rhc.controller_of("sda1"), "sda1")

    def test_crit_and_sticky_until_acknowledged(self):
        memo = {}
        f = rhc.check_storage_errors(StorageHost(FAILING), "-5min", "2026-10-16T00:05:00", {}, {}, memo)
        self.assertEqual([x.level for x in f], ["crit"])
        self.assertIn("nvme1 (Sabrent Rocket 4 Plus SN 48816072200111)", f[0].message)
        self.assertIn("2 critical medium error", f[0].message)
        self.assertEqual(f[0].value, 5)
        # next run: the journal window is quiet, but the drive stays critical
        previous = {"storage": memo}
        memo2 = {}
        f = rhc.check_storage_errors(StorageHost(""), "x", "2026-10-16T00:10:00", previous, {}, memo2)
        self.assertEqual([x.level for x in f], ["crit"])
        # acknowledged after the last error: back to ok, and dropped from the carried state
        acks = {"48816072200111": {"acknowledged_at": "2026-10-16T00:07:00", "media_errors": 1495}}
        memo3 = {}
        f = rhc.check_storage_errors(StorageHost(""), "x", "2026-10-16T00:15:00", {"storage": memo2}, acks, memo3)
        self.assertEqual([x.level for x in f], ["ok"])
        self.assertEqual(memo3["storage_errors"], {})

    def test_unreadable_journal_warns(self):
        host = StorageHost()
        host.commands = {("journalctl", "-k"): (1, "", "Failed to parse timestamp")}
        self.assertEqual([x.level for x in rhc.check_storage_errors(host, "x", "t", {}, {}, {})], ["warn"])


class FirmwareTests(unittest.TestCase):
    def test_same_model_mismatch_and_host_config_mismatch(self):
        config = dict(CONFIG, storage_devices=[{"name": "nvme0n1", "serial_number": 48816072200219, "firmware_version": "R4PTLC.3"}])
        f = rhc.check_nvme_firmware(StorageHost(), config)
        self.assertEqual([x.level for x in f], ["warn", "warn"])
        self.assertIn("R4PTLC.3: nvme1; R4PTLC.4: nvme0", f[0].message)
        self.assertIn("host config says R4PTLC.3", f[1].message)
        drives = {k: dict(v, firmware_rev="R4PTLC.4") if k == "nvme1" else v for k, v in DRIVES.items()}
        self.assertEqual([x.level for x in rhc.check_nvme_firmware(StorageHost(drives=drives), CONFIG)], ["ok"])


class SmartTests(unittest.TestCase):
    def run_smart(self, doc, previous=None, acks=None, th=None):
        memo = {}
        f = rhc.check_nvme_smart(StorageHost(snap_doc=doc), th or TH, datetime.now().timestamp(), previous or {}, acks or {}, memo)
        return f, memo

    def test_not_installed_is_info_and_garbage_or_stale_warns(self):
        self.assertEqual([x.level for x in self.run_smart(None)[0]], ["info"])
        self.assertEqual([x.level for x in self.run_smart("{not json")[0]], ["warn"])
        stale = snapshot(time=datetime.now().astimezone() - timedelta(hours=1))
        self.assertIn("stale", self.run_smart(stale)[0][0].message)

    def test_healthy_drives_are_ok(self):
        f, memo = self.run_smart(snapshot())
        self.assertEqual([x.level for x in f], ["ok"])
        self.assertEqual(memo["smart"]["48816072200111"]["media_errors"], 0)

    def test_media_errors_crit_until_acknowledged_then_only_new_ones(self):
        doc = snapshot(nvme1=smart(media_errors=1495, unsafe_shutdowns=10))
        f, _ = self.run_smart(doc)
        self.assertEqual([x.level for x in f], ["crit"])
        self.assertIn("1495 media errors (never acknowledged", f[0].message)
        acks = {"48816072200111": {"acknowledged_at": "t", "media_errors": 1495}}
        self.assertEqual([x.level for x in self.run_smart(doc, acks=acks)[0]], ["ok"])
        f, _ = self.run_smart(snapshot(nvme1=smart(media_errors=1500)), acks=acks)
        self.assertIn("5 new since the acknowledgement of 1495", f[0].message)

    def test_flags_and_increases(self):
        previous = {"storage": {"smart": {"48816072200219": {"num_err_log_entries": 5, "unsafe_shutdowns": 16,
                                                             "warning_temp_time": 0, "critical_comp_time": 0}}}}
        doc = snapshot(nvme0=smart(critical_warning=0x04, avail_spare=9, spare_thresh=10, percent_used=85,
                                   num_err_log_entries=7, unsafe_shutdowns=17, warning_temp_time=3))
        f, memo = self.run_smart(doc, previous)
        got = sorted((x.level, x.message.split(": ", 1)[1].split(" (")[0]) for x in f)
        self.assertEqual(got, [
            ("crit", "available spare 9% at or below its threshold 10%"),
            ("crit", "critical_warning 0x04"),
            ("warn", "85% of rated endurance used"),
            ("warn", "error-log entries 5 -> 7"),
            ("warn", "minutes above the warning temperature 0 -> 3"),
            ("warn", "unsafe shutdowns 16 -> 17"),
        ])
        self.assertEqual(memo["smart"]["48816072200219"]["unsafe_shutdowns"], 17)

    def test_nvme_cli_2_field_names_and_percent_strings(self):
        s = {"critical_warning": 0, "available_spare": 5, "available_spare_threshold": 10, "percentage_used": "3%", "media_errors": 0}
        self.assertEqual(rhc.smart_value(s, "percent_used"), 3)
        f, _ = self.run_smart(snapshot(nvme2=s))
        self.assertEqual([x.level for x in f], ["crit"])

    def test_smart_log_failure_warns(self):
        doc = snapshot()
        doc["controllers"]["nvme2"].update(smart=None, error="permission denied")
        f, _ = self.run_smart(doc)
        self.assertEqual([(x.level, "permission denied" in x.message) for x in f], [("warn", True)])


class AcknowledgeTests(unittest.TestCase):
    def test_writes_baseline_from_snapshot_by_serial_or_device(self):
        host = StorageHost(snap_doc=snapshot(nvme1=smart(media_errors=1495)))
        with tempfile.TemporaryDirectory() as tmp:
            msg = rhc.acknowledge_drive(host, Path(tmp), "nvme1n1p1", "rescued; returning to Sabrent")
            self.assertIn("baseline 1495", msg)
            acks = json.loads((Path(tmp) / rhc.DRIVE_ACKS).read_text())
            self.assertEqual(acks["48816072200111"]["media_errors"], 1495)
            self.assertEqual(acks["48816072200111"]["note"], "rescued; returning to Sabrent")
            rhc.acknowledge_drive(host, Path(tmp), "48816072200219")
            self.assertEqual(sorted(json.loads((Path(tmp) / rhc.DRIVE_ACKS).read_text())), ["48816072200111", "48816072200219"])


class PreflightTests(unittest.TestCase):
    DEVS = {"/mnt/Data1": (259, 1, "/sys/devices/pci0000:20/0000:20:01.2/0000:21:00.0/nvme/nvme1/nvme1n1/nvme1n1p1"),
            "/mnt/Data2": (259, 4, "/sys/devices/pci0000:40/0000:40:01.1/0000:41:00.0/nvme/nvme0/nvme0n1/nvme0n1p2")}
    DISKS = {"/mnt/Data1": Statvfs(1_000_000_000, 850_000_000, 4096), "/mnt/Data2": Statvfs(1_000_000_000, 100_000_000, 4096)}

    def gate(self, root, expected_gb=None, journal="", doc=None, previous=None, acks=None):
        host = StorageHost(journal, snap_doc=doc or snapshot(nvme1=smart(media_errors=1495)), devs=self.DEVS, disks=self.DISKS)
        f = rhc.preflight(host, CONFIG, TH, root, expected_gb, "x", previous or {}, acks or {})
        return rhc.worst_level(f), f

    def test_failing_drive_blocks_and_only_its_drive_is_judged(self):
        worst, f = self.gate("/mnt/Data1", 333, journal=FAILING)
        self.assertEqual(worst, "crit")
        self.assertEqual(sorted({x.check for x in f if x.level == "crit"}), ["nvme_smart", "storage_errors"])
        self.assertTrue(all("nvme0" not in x.message for x in f if x.level != "info"))
        worst, f = self.gate("/mnt/Data2", 333)    # healthy drive, 410 GB free > 333 GB + 10%
        self.assertEqual(worst, "ok")
        self.assertIn("/mnt/Data2 is on nvme0n1p2 (controller nvme0)", f[0].message)

    def test_not_enough_space_for_the_run_blocks(self):
        worst, f = self.gate("/mnt/Data2", 380)    # needs 418 GB, has 410
        self.assertEqual(worst, "crit")
        self.assertIn("needs 380 GB + 10% = 418 GB", next(x.message for x in f if x.level == "crit"))

    def test_missing_root_blocks(self):
        self.assertEqual(self.gate("/mnt/Nope")[0], "crit")

    def test_main_preflight_exit_code_and_no_state_written(self):
        host = StorageHost(FAILING, snap_doc=snapshot(nvme1=smart(media_errors=1495)), devs=self.DEVS, disks=self.DISKS)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.yml"
            cfg.write_text("{}\n")
            state = Path(tmp) / "state"
            orig = rhc.Host
            rhc.Host = lambda: host
            try:
                import contextlib, io
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    rc = rhc.main(["--config", str(cfg), "--preflight", "--recording-root", "/mnt/Data1",
                                   "--expected-gb", "333", "--state-dir", str(state), "--json"])
            finally:
                rhc.Host = orig
            self.assertEqual(rc, 2)
            self.assertEqual(json.loads(out.getvalue())["preflight"], {"recording_root": "/mnt/Data1", "expected_gb": 333.0})
            self.assertFalse(state.exists())


class SnapshotScriptTests(unittest.TestCase):
    def test_collects_every_controller_with_a_fixed_command(self):
        calls = []

        def fake_run(args, **kw):
            calls.append((args, kw.get("env")))
            if args[2] == "/dev/nvme2":
                return SimpleNamespace(returncode=1, stdout="", stderr="Permission denied")
            return SimpleNamespace(returncode=0, stdout=json.dumps(smart()), stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            for ctrl, d in DRIVES.items():
                base = Path(tmp) / ctrl
                (base / d["namespaces"][0]).mkdir(parents=True)
                (base / ("ng" + d["namespaces"][0][4:])).mkdir()
                for k in ("model", "serial", "firmware_rev"):
                    (base / k).write_text(d[k] + "\n")
            (Path(tmp) / "nvme-fabrics").mkdir()
            doc = snap.collect(sys_nvme=tmp, nvme="/usr/sbin/nvme", run=fake_run)
        self.assertEqual(sorted(doc["controllers"]), ["nvme0", "nvme1", "nvme2"])
        self.assertEqual(calls[0][0], ["/usr/sbin/nvme", "smart-log", "/dev/nvme0", "-o", "json"])
        self.assertEqual(calls[0][1]["PATH"], "/usr/sbin:/usr/bin:/sbin:/bin")
        self.assertEqual(doc["controllers"]["nvme1"]["firmware_rev"], "R4PTLC.3")
        self.assertEqual(doc["controllers"]["nvme0"]["namespaces"], ["nvme0n1"])
        self.assertEqual(doc["controllers"]["nvme2"], {**doc["controllers"]["nvme2"], "smart": None, "error": "Permission denied"})
        self.assertEqual(snap.smart_log("nvme0; rm -rf /")[1], "refusing controller name 'nvme0; rm -rf /'")

    def test_snapshot_is_written_atomically_and_world_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "smart.json"
            snap.write_snapshot({"a": 1}, str(path))
            self.assertEqual(json.loads(path.read_text()), {"a": 1})
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["smart.json"])

    def test_script_stays_read_only(self):
        """Only smart-log is executed and only the snapshot (via a temp file) is written."""
        tree = ast.parse(SNAP.read_text())
        calls = {f"{n.func.value.id}.{n.func.attr}" if isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
                 else getattr(n.func, "id", getattr(n.func, "attr", "")) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        self.assertFalse(calls & {"os.system", "os.popen", "os.remove", "os.rename", "shutil.rmtree", "subprocess.Popen",
                                  "subprocess.check_call", "subprocess.call", "os.execv", "os.execvp"}, calls)
        literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        self.assertIn("smart-log", literals)
        for verb in ("format", "sanitize", "fw-download", "fw-commit", "set-feature", "write", "admin-passthru", "io-passthru", "reset"):
            self.assertNotIn(verb, literals)
        self.assertEqual(sum(1 for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "run"), 1)


if __name__ == "__main__":
    unittest.main()
