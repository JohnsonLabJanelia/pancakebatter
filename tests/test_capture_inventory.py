import json
import unittest

import capture_inventory as ci

ETHTOOL_UP = """Settings for enp1:
\tSupported ports: [ FIBRE ]
\tSupported link modes:   1000baseT/Full
\t                        10000baseT/Full
\t                        25000baseCR/Full
\tSpeed: 25000Mb/s
\tAuto-negotiation: on
\tLink detected: yes
"""
ETHTOOL_DOWN = """Settings for enp2:
\tSupported link modes:   1000baseT/Full
\t                        50000baseCR2/Full
\tSpeed: Unknown!
\tAuto-negotiation: on
\tLink detected: no
"""
LSBLK = json.dumps({"blockdevices": [
    {"name": "loop0", "type": "loop", "size": 1000},
    {"name": "nvme0n1", "type": "disk", "model": "Acme 8TB ", "serial": "SN1", "size": 8001563222016,
     "rev": "FW1", "phy-sec": 512, "tran": "nvme", "children": [
         {"name": "nvme0n1p1", "type": "part", "fstype": "ext4", "mountpoint": "/data"}]},
]})


class ParserTests(unittest.TestCase):
    def test_nvidia_smi_mixed_gpus(self):
        out = ("NVIDIA A16, 580.1, GPU-aa, 00000000:C5:00.0, 15356 MiB, 8.6\n"
               "NVIDIA RTX PRO 4000 Blackwell, 580.1, GPU-bb, 00000000:E1:00.0, 24467 MiB, 12.0\n")
        gpus = ci.parse_nvidia_smi_csv(out)
        self.assertEqual([g["cuda_capability"] for g in gpus], ["sm_86", "sm_120"])
        self.assertEqual(gpus[0]["pcie_id"], "c5:00.0")
        self.assertEqual(gpus[1]["memory_mib"], 24467)

    def test_ethtool_up_and_down(self):
        up = ci.parse_ethtool(ETHTOOL_UP)
        self.assertEqual((up["speed"], up["autoneg"], up["link"]), (25000, True, True))
        self.assertEqual(max(up["supported_mbps"]), 25000)
        down = ci.parse_ethtool(ETHTOOL_DOWN)
        self.assertIsNone(down["speed"])
        self.assertFalse(down["link"])
        self.assertEqual(max(down["supported_mbps"]), 50000)

    def test_ethtool_module(self):
        trx = ci.parse_ethtool_m(
            "Vendor name : InnoLight\nVendor PN : TR-PY13L-V00\nVendor SN : INL1\n"
            "Laser wavelength : 1310.000nm\nBR, Nominal : 25500MBd\nLength (SMF) : 10km\n")
        self.assertEqual(trx, {"brand": "InnoLight", "model": "TR-PY13L-V00", "serial_number": "INL1",
                               "speed": 25, "wavelength": 1310, "max_distance": 10000, "fiber_type": "SMF"})

    def test_lsblk_keeps_only_physical_disks(self):
        devs = ci.parse_lsblk(LSBLK)
        self.assertEqual(len(devs), 1)
        self.assertEqual(devs[0]["model"], "Acme 8TB")
        self.assertEqual(devs[0]["capacity"], 8.0)
        self.assertEqual(devs[0]["partitions"][0]["mount_point"], "/data")


DMI = """# dmidecode 3.5
Getting SMBIOS data from sysfs.

Handle 0x0030, DMI type 9, 17 bytes
System Slot Information
\tDesignation: PCIEX16_1
\tType: PCI Express 5 x16
\tCurrent Usage: In Use
\tBus Address: 0000:21:00.1

Handle 0x0031, DMI type 9, 17 bytes
System Slot Information
\tDesignation: PCIEX16_2
\tType: PCI Express 5 x16
\tCurrent Usage: Available
\tBus Address: 0000:c0:00.0

Handle 0x0032, DMI type 9, 17 bytes
System Slot Information
\tDesignation: M.2_1
\tType: M.2 Socket 3
\tCurrent Usage: Unknown
"""


class SlotTests(unittest.TestCase):
    def test_parse_dmi_slots(self):
        slots = ci.parse_dmi_slots(DMI)
        self.assertEqual([s["designation"] for s in slots], ["PCIEX16_1", "PCIEX16_2", "M.2_1"])
        self.assertEqual(slots[0]["bus"], "21:00")
        self.assertEqual(slots[0]["usage"], "In Use")
        self.assertIsNone(slots[2]["bus"])

    def test_slot_lookup_matches_device_bus(self):
        slots = ci.parse_dmi_slots(DMI)
        self.assertEqual(ci.slot_for_device(slots, "21:00.3"), "PCIEX16_1")
        self.assertIsNone(ci.slot_for_device(slots, "d3:00.0"))
        self.assertIsNone(ci.slot_for_device(slots, None))
        self.assertEqual(ci.parse_dmi_slots(""), [])


# Real `ethtool -m` output (FS SFP-25GLR-31 in a ConnectX-7 on dumpling, 2026-10-02), trimmed.
REAL_SFP = """\
\tIdentifier                                : 0x03 (SFP)
\tConnector                                 : 0x07 (LC)
\tTransceiver type                          : Extended: 100G Base-LR4 or 25GBase-LR
\tEncoding                                  : 0x06 (64B/66B)
\tBR, Nominal                               : 25750MBd
\tLength (SMF,km)                           : 10km
\tLength (SMF)                              : 0m
\tLength (50um)                             : 0m
\tLength (62.5um)                           : 0m
\tLength (Copper)                           : 0m
\tLength (OM3)                              : 0m
\tLaser wavelength                          : 1310nm
\tVendor name                               : FS
\tVendor PN                                 : SFP-25GLR-31
\tVendor SN                                 : S2435306849
"""


class RealModuleTests(unittest.TestCase):
    def test_real_single_mode_sfp(self):
        self.assertEqual(ci.parse_ethtool_m(REAL_SFP), {
            "brand": "FS", "model": "SFP-25GLR-31", "serial_number": "S2435306849",
            "speed": 25, "wavelength": 1310, "max_distance": 10000, "fiber_type": "SMF"})

    def test_multimode_and_dac_variants(self):
        mm = ci.parse_ethtool_m("Length (OM3)  : 70m\nLength (SMF,km) : 0km\nBR, Nominal : 10312MBd\n")
        self.assertEqual((mm["fiber_type"], mm["max_distance"], mm["speed"]), ("MMF", 70, 10))
        dac = ci.parse_ethtool_m("Length (Copper) : 3m\nBR, Nominal : 25781MBd\n")
        self.assertEqual((dac["fiber_type"], dac["max_distance"], dac["speed"]), ("DAC", 3, 25))
        self.assertEqual(ci.parse_ethtool_m("")["fiber_type"], None)


class VpdTests(unittest.TestCase):
    @staticmethod
    def vpd(**fields):
        ro = b"".join(k.encode() + bytes([len(v)]) + v.encode() for k, v in fields.items())
        name = b"Card"
        return (b"\x82" + len(name).to_bytes(2, "little") + name +
                b"\x90" + len(ro).to_bytes(2, "little") + ro + b"\x78")

    def test_part_and_serial(self):
        got = ci.parse_vpd(self.vpd(PN="MCX713104AS-ADAT", SN="MT2336XZ09PC"))
        self.assertEqual((got["PN"], got["SN"]), ("MCX713104AS-ADAT", "MT2336XZ09PC"))

    def test_garbage_is_empty(self):
        self.assertEqual(ci.parse_vpd(b"\xff\xff\xff"), {})
        self.assertEqual(ci.parse_vpd(b""), {})

    def test_fiber_types(self):
        self.assertEqual(ci.parse_ethtool_m("Length (OM3 50um) : 70m\n")["fiber_type"], "MMF")
        self.assertEqual(ci.parse_ethtool_m("Length (Copper) : 3m\n")["fiber_type"], "DAC")


class ValidationTests(unittest.TestCase):
    def config(self, nic):
        return {"schema": {"name": "system_config", "version": 1},
                "system_info": {"hostname": "t", "network_renderer": "NetworkManager"},
                "nics": [{"enp1": nic}], "cameras": {}}

    def nic(self, **over):
        nic = {"altname": None, "role": "unknown", "managed": False, "expected_link": False,
               "mac_address": "aa:bb:cc:dd:ee:ff", "mtu": 1500, "ip_address": ci.UNASSIGNED_IP,
               "link_settings": {"speed": 10000, "autoneg": True}}
        nic.update(over)
        return nic

    def test_captured_shaped_nic_passes(self):
        self.assertEqual(ci.validate(self.config(self.nic())), [])

    def test_bad_nic_is_reported(self):
        self.assertTrue(ci.validate(self.config(self.nic(role="bogus"))))


if __name__ == "__main__":
    unittest.main()
