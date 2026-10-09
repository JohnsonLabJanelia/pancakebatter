import unittest

import pcie_topology as pt

ROWS = [
    {"bridge": "00", "pci": "01:00.0", "kind": "gpu", "name": "GPU0 A6000", "path": ["00:03.1", "01:00.0"]},
    {"bridge": "20", "pci": "23:00.0", "kind": "nvme", "name": "nvme0", "path": ["20:01.1", "21:00.0", "22:00.0", "23:00.0"],
     "serial": "S219", "mounts": ["/mnt/Data2"], "partitions": [{"partition": "nvme0n1p2", "uuid": "bb4e-uuid", "mounts": ["/mnt/Data2"]}]},
    {"bridge": "20", "pci": "24:00.0", "kind": "nic", "name": "ethernet_1", "path": ["20:01.1", "21:00.0", "22:02.0", "24:00.0"]},
    {"bridge": "60", "pci": "61:00.0", "kind": "nic", "name": "mlnx1_p1_25g", "path": ["60:01.1", "61:00.0"]},
]


class TopologyTests(unittest.TestCase):
    def test_group_and_table(self):
        groups = pt.group(ROWS)
        self.assertEqual(list(groups), ["00", "20", "60"])
        table = pt.format_table(ROWS)
        self.assertIn("host bridge pci0000:20", table)
        self.assertIn("mounts /mnt/Data2", table)
        self.assertIn("via 20:01.1 > 21:00.0 > 22:02.0", table)

    def test_record_block_keeps_existing_notes(self):
        import yaml
        existing = {"host_bridges": {"20": {"note": "crowded", "devices": []}}}
        doc = yaml.safe_load(pt.record_block(ROWS, existing))["pcie_topology"]["host_bridges"]
        self.assertEqual(doc["20"]["note"], "crowded")
        self.assertNotIn("note", doc["00"])
        drive = doc["20"]["devices"][0]
        self.assertEqual((drive["pci"], drive["serial"], drive["partitions"]), ("23:00.0", "S219", [{"uuid": "bb4e-uuid", "mounts": ["/mnt/Data2"]}]))
        self.assertIn("nvmeN is not stable", drive["name"])

    def test_compare_reports_moves_additions_and_removals_only(self):
        recorded = {"host_bridges": {
            "00": {"devices": [{"pci": "01:00.0", "kind": "gpu", "name": "old name"}]},
            "20": {"devices": [{"pci": "29:00.0", "kind": "nvme", "serial": "S219"}, {"pci": "24:00.0", "kind": "nic"}, {"pci": "2a:00.0", "kind": "nvme", "serial": "S297"}]},
            "40": {"devices": [{"pci": "61:00.0", "kind": "nic"}]},
        }}
        problems = pt.compare(recorded, ROWS)
        self.assertEqual(sorted(problems), sorted([
            "nvme S297 recorded under bridge 20 is gone",
            "nvme S219 moved within bridge 20: recorded 29:00.0, live 23:00.0",   # drives are followed by serial
            "nic 61:00.0 moved: recorded bridge 40, live 60",
        ]))   # a renamed GPU is not a difference
        self.assertEqual(pt.compare(None, ROWS), ["no pcie_topology recorded (run: pcie_topology.py --record)"])

    def test_pci_path_parsing(self):
        bridge, path = pt.pci_path("/sys/devices/pci0000:20/0000:20:01.1/0000:21:00.0/0000:22:02.0/0000:24:00.0")
        self.assertEqual((bridge, path), ("20", ["20:01.1", "21:00.0", "22:02.0", "24:00.0"]))


if __name__ == "__main__":
    unittest.main()
