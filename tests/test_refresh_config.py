import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import capture_inventory as ci
import refresh_config as rc


def nic(mac, pcie, **over):
    n = {"altname": "eth0", "role": "camera", "managed": True, "expected_link": True, "mac_address": mac,
         "brand": "Mellanox", "model": "CX7", "part_number": None, "pcie_id": pcie, "pcie_slot": None,
         "serial_number": None, "driver": "mlx5_core", "firmware_version": "28.1", "mtu": 9000,
         "ip_address": "192.168.110.1/24", "link_settings": {"speed": 25000, "autoneg": True},
         "transceiver": {"brand": None, "model": None, "serial_number": None, "speed": None,
                         "wavelength": None, "max_distance": None, "fiber_type": None}}
    n.update(over)
    return n


def config(host="dumpling"):
    return {"schema": {"name": "system_config", "version": 1},
            "system_info": {"hostname": host, "network_renderer": "NetworkManager", "kernel_version": "7.0.0-34"},
            "storage_devices": [{"name": "nvme1n1", "serial_number": "S1", "firmware_version": "A",
                                 "sequential_read_speed": 7000}],
            "gpus": [{"model": "A16", "uuid": "GPU-1", "driver_version": "580.1"}],
            "nics": [{"mlnx1_p1_25g": nic("60:5e:65:5c:8b:ee", "f1:00.0")},
                     {"mlnx1_p2_25g": nic("60:5e:65:5c:8b:ef", "f1:00.1")}],
            "cameras": {}}


def module(serial):
    return {"brand": "FS", "model": "SFP-25GLR-31", "serial_number": serial, "speed": 25,
            "wavelength": 1310, "max_distance": 10000, "fiber_type": "SMF"}


class MergeTests(unittest.TestCase):
    def fresh(self):
        f = config()
        f["nics"][1]["mlnx1_p2_25g"]["transceiver"] = module("S2")
        f["nics"][0]["mlnx1_p1_25g"]["role"] = "unknown"           # human-owned: must not be copied
        f["nics"][0]["mlnx1_p1_25g"]["ip_address"] = "0.0.0.0/0"   # human-owned
        f["nics"][0]["mlnx1_p1_25g"]["firmware_version"] = "28.2"  # a fact: must be copied
        return f

    def test_adds_new_module_updates_facts_and_never_touches_human_fields(self):
        existing = config()
        merged, changes, _ = rc.merge_facts(existing, self.fresh())
        by = {k: v for i in merged["nics"] for k, v in i.items()}
        self.assertEqual(by["mlnx1_p2_25g"]["transceiver"]["serial_number"], "S2")
        self.assertEqual(by["mlnx1_p1_25g"]["firmware_version"], "28.2")
        self.assertEqual((by["mlnx1_p1_25g"]["role"], by["mlnx1_p1_25g"]["ip_address"]), ("camera", "192.168.110.1/24"))
        self.assertTrue(any("firmware_version: '28.1' -> '28.2'" in c for c in changes))
        self.assertEqual(existing, config(), "input must not be mutated")

    def test_never_erases_existing_values_with_empty_readings(self):
        existing = config()
        existing["nics"][1]["mlnx1_p2_25g"]["transceiver"] = module("S2")
        existing["nics"][0]["mlnx1_p1_25g"]["serial_number"] = "TYPED-BY-HAND"
        merged, changes, notes = rc.merge_facts(existing, config())  # fresh has no module / no serials
        by = {k: v for i in merged["nics"] for k, v in i.items()}
        self.assertEqual(by["mlnx1_p2_25g"]["transceiver"]["brand"], "FS")
        self.assertEqual(by["mlnx1_p1_25g"]["serial_number"], "TYPED-BY-HAND")
        self.assertEqual(changes, [])
        self.assertTrue(any("kept recorded FS" in n for n in notes))

    def test_identical_facts_mean_no_changes(self):
        self.assertEqual(rc.merge_facts(config(), config())[1], [])

    def test_refuses_a_config_for_another_host(self):
        with self.assertRaises(rc.RefreshError):
            rc.merge_facts(config("pancake0"), config("dumpling"))

    def test_nics_matched_by_mac_even_if_renamed_and_reports_unmatched(self):
        fresh = config()
        fresh["nics"][0] = {"enp241s0f0np0": fresh["nics"][0]["mlnx1_p1_25g"]}  # kernel name, same MAC
        fresh["nics"].append({"enp9s0": nic("aa:aa:aa:aa:aa:aa", "09:00.0")})
        existing = config()
        existing["nics"].append({"gone": nic("bb:bb:bb:bb:bb:bb", "0a:00.0")})
        _, changes, notes = rc.merge_facts(existing, fresh)
        self.assertEqual(changes, [])
        self.assertTrue(any("new NIC enp9s0" in n for n in notes))
        self.assertTrue(any("gone" in n and "not detected" in n for n in notes))

    def test_storage_keeps_extras_and_gpus_without_uuid_are_replaced(self):
        fresh = config()
        fresh["storage_devices"][0].pop("sequential_read_speed", None)
        fresh["storage_devices"][0]["firmware_version"] = "B"
        existing = config()
        merged, _, _ = rc.merge_facts(existing, fresh)
        self.assertEqual(merged["storage_devices"][0]["sequential_read_speed"], 7000)
        self.assertEqual(merged["storage_devices"][0]["firmware_version"], "B")
        existing["gpus"] = [{"model": "A16", "driver_version": "535"}]  # pancake0 style: no uuid
        merged, changes, _ = rc.merge_facts(existing, fresh)
        self.assertEqual(merged["gpus"], fresh["gpus"])
        self.assertTrue(any("replaced 1 item" in c for c in changes))


class SerialTypeTests(unittest.TestCase):
    """YAML reads 48816072200219 as an int, capture reports "48816072200219"; they are the same disk."""

    def test_int_serials_match_string_serials_without_duplicating(self):
        existing = config()
        existing["storage_devices"] = [{"name": "nvme0n1", "serial_number": 48816072200219, "model": "X"},
                                       {"name": "nvme1n1", "serial_number": 48816072200222, "model": "X"}]
        fresh = config()
        fresh["storage_devices"] = [{"name": "nvme0n1", "serial_number": "48816072200219", "model": "X"},
                                    {"name": "nvme1n1", "serial_number": "48816072200222", "model": "X"}]
        merged, changes, notes = rc.merge_facts(existing, fresh)
        self.assertEqual(changes, [])
        self.assertFalse([n for n in notes if "storage" in n], notes)
        self.assertEqual(merged["storage_devices"], existing["storage_devices"])  # int type kept

    def test_renamed_device_node_follows_the_serial(self):
        existing = config()
        existing["storage_devices"] = [{"name": "nvme0n1", "serial_number": 111, "device_path": "/dev/nvme0n1"}]
        fresh = config()
        fresh["storage_devices"] = [{"name": "nvme2n1", "serial_number": "111", "device_path": "/dev/nvme2n1"}]
        merged, changes, _ = rc.merge_facts(existing, fresh)
        self.assertEqual(len(merged["storage_devices"]), 1)
        self.assertEqual(merged["storage_devices"][0]["name"], "nvme2n1")
        self.assertEqual(merged["storage_devices"][0]["serial_number"], 111)


class RefreshCliTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "config.yml"
        self.path.write_text("# my header\n" + yaml.safe_dump(config(), sort_keys=False))
        fresh = config()
        fresh["nics"][1]["mlnx1_p2_25g"]["transceiver"] = module("S2")
        patcher = patch.object(ci, "build_config", return_value=fresh)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_preview_writes_nothing(self):
        before = self.path.read_text()
        self.assertEqual(ci.main(["--refresh", "--config", str(self.path)]), 0)
        self.assertEqual(self.path.read_text(), before)
        self.assertEqual(list(self.dir.glob("*.bak.*")), [])

    def test_write_applies_with_backup_and_header(self):
        self.assertEqual(ci.main(["--refresh", "--write", "--config", str(self.path)]), 0)
        self.assertTrue(self.path.read_text().startswith("# my header\n"))
        saved = yaml.safe_load(self.path.read_text())
        self.assertEqual(saved["nics"][1]["mlnx1_p2_25g"]["transceiver"]["serial_number"], "S2")
        self.assertEqual(len(list(self.dir.glob("config.yml.bak.*"))), 1)

    def test_second_write_is_a_noop(self):
        ci.main(["--refresh", "--write", "--config", str(self.path)])
        ci.main(["--refresh", "--write", "--config", str(self.path)])
        self.assertEqual(len(list(self.dir.glob("config.yml.bak.*"))), 1)

    def test_write_requires_refresh_and_missing_config_is_an_error(self):
        with self.assertRaises(SystemExit):
            ci.main(["--write"])
        with self.assertRaises(SystemExit):
            ci.main(["--refresh", "--config", str(self.dir / "nope.yml")])


if __name__ == "__main__":
    unittest.main()
