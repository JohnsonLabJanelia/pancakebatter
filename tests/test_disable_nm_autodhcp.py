import unittest
from pathlib import Path
from unittest.mock import patch

import disable_nm_autodhcp as d

CONFIG = {"nics": [
    {"enp211s0f0np0": {"role": "management", "mac_address": "B0:82:E2:37:37:5F"}},
    {"enp211s0f1np1": {"role": "unknown", "mac_address": "b0:82:e2:37:37:60"}},
    {"mlnx1_p1_25g": {"role": "camera", "mac_address": "60:5E:65:5C:8B:EE"}},
    {"mlnx2_p1_25g": {"role": "spare", "mac_address": "54:9b:24:5d:34:22"}},
]}

# name, uuid, type per `nmcli -t -f NAME,UUID,TYPE connection show`; profile -> (ifname, mac)
PROFILES = {
    "u-ssh":   ("Wired connection 1", "enp211s0f0np0", ""),
    "u-x710":  ("Wired connection 2", "enp211s0f1np1", ""),
    "u-gen3":  ("Wired connection 3", "mlnx1_p1_25g", ""),
    "u-gen7":  ("Wired connection 7", "", "54:9B:24:5D:34:22"),   # bound by MAC only
    "u-ours":  ("mlnx1_p1_25g", "mlnx1_p1_25g", ""),              # repo-created static profile
    "u-other": ("Wired connection 9", "enp99s0", ""),
}


def fake_nmcli(args):
    if args[:3] == ["-t", "-f", "NAME,UUID,TYPE"]:
        return "".join(f"{n}:{u}:802-3-ethernet\n" for u, (n, _, _) in PROFILES.items()) + "lo:u-lo:loopback\n"
    uuid = args[-1]
    if "connection.interface-name" in args:
        return PROFILES[uuid][1] + "\n"
    if "802-3-ethernet.mac-address" in args:
        return PROFILES[uuid][2] + "\n"
    raise AssertionError(args)


class AutoDhcpTests(unittest.TestCase):
    def test_only_camera_and_spare_nics_are_listed_with_lowercase_macs(self):
        self.assertEqual(d.static_nics(CONFIG),
                         {"mlnx1_p1_25g": "60:5e:65:5c:8b:ee", "mlnx2_p1_25g": "54:9b:24:5d:34:22"})

    def test_conf_is_additive_and_sorted(self):
        text = d.conf_text(d.static_nics(CONFIG), Path("c.yml"))
        self.assertIn("[main]\nno-auto-default+=54:9b:24:5d:34:22,60:5e:65:5c:8b:ee\n", text)
        self.assertNotIn("b0:82", text)  # management/unknown NICs never listed

    def test_deletes_only_generic_profiles_of_static_nics(self):
        found = d.generic_profiles_to_delete(d.static_nics(CONFIG), nmcli=fake_nmcli)
        self.assertEqual(sorted(u for u, _, _ in found), ["u-gen3", "u-gen7"])

    def test_never_deletes_ssh_management_x710_ours_or_unrelated(self):
        uuids = {u for u, _, _ in d.generic_profiles_to_delete(d.static_nics(CONFIG), nmcli=fake_nmcli)}
        self.assertTrue(uuids.isdisjoint({"u-ssh", "u-x710", "u-ours", "u-other"}))

    def test_apply_requires_root_but_dry_run_does_not(self):
        with patch.object(d.os, "geteuid", return_value=1000):
            with self.assertRaises(SystemExit):
                d.main([])


if __name__ == "__main__":
    unittest.main()
