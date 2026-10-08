import io
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import yaml

import configio

COMMENTED = """# header line
# second header line
schema:
  name: system_config
  version: 1

# System facts
system_info:
  hostname: t          # the short hostname
  ubuntu_version: "22.04"
  esdk_version: "1.0"  # stale on purpose
  network_renderer: NetworkManager

# NICs
nics:
  - mlnx1_p1_25g:
      role: camera     # camera port
      mac_address: aa:bb:cc:dd:ee:01
cameras: {}
"""


def count_comments(text):
    return sum(1 for l in text.splitlines() if "#" in l)


class SyncTests(unittest.TestCase):
    def test_sync_updates_in_place_and_drops_removed_keys(self):
        node = {"a": 1, "b": {"c": 2, "d": 3}, "l": [1, 2]}
        out = configio._sync(node, {"a": 1, "b": {"c": 9}, "l": [1, 5], "e": "new"})
        self.assertIs(out, node)
        self.assertEqual(out, {"a": 1, "b": {"c": 9}, "l": [1, 5], "e": "new"})

    def test_lists_of_different_length_are_replaced(self):
        self.assertEqual(configio._sync([1, 2, 3], [1, 2]), [1, 2])


@unittest.skipUnless(configio._ruamel(), "ruamel.yaml not installed for this interpreter")
class RoundTripSaveTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "config.yml"
        self.path.write_text(COMMENTED)
        self.addCleanup(self.dir.cleanup)

    def test_save_keeps_comments_quotes_and_order_while_applying_changes(self):
        cfg = configio.load(self.path)
        cfg["system_info"]["esdk_version"] = "2.55.02"
        cfg["cameras"]["E0-55-97-1E-B6-B8"] = {"model": "HB-2800SC", "serial_number": 2012856,
                                                "ip_address": "192.168.130.2", "nic_port": "mlnx1_p1_25g"}
        with unittest.mock.patch.object(configio, "validate", return_value=[]):
            self.assertEqual(configio.save(self.path, cfg), [])
        text = self.path.read_text()
        self.assertEqual(count_comments(text), count_comments(COMMENTED))
        self.assertIn("# stale on purpose", text)
        self.assertIn('ubuntu_version: "22.04"', text)          # quoting preserved
        self.assertIn("esdk_version: 2.55.02", text.replace('"', ""))
        self.assertIn("E0-55-97-1E-B6-B8", text)
        self.assertEqual(yaml.safe_load(text), cfg)             # and the content is exactly what was asked
        self.assertTrue(list(Path(self.dir.name).glob("config.yml.bak.*")))

    def test_sequence_style_matches_the_repo_files(self):
        cfg = configio.load(self.path)
        with unittest.mock.patch.object(configio, "validate", return_value=[]):
            configio.save(self.path, cfg)
        self.assertIn("nics:\n  - mlnx1_p1_25g:", self.path.read_text())


class FallbackSaveTests(unittest.TestCase):
    def test_without_ruamel_the_header_survives_and_a_warning_names_the_loss(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.yml"
            path.write_text(COMMENTED)
            err = io.StringIO()
            with unittest.mock.patch.object(configio, "_ruamel", return_value=None), \
                 unittest.mock.patch.object(configio, "validate", return_value=[]), \
                 unittest.mock.patch.object(sys, "stderr", err):
                configio.save(path, configio.load(path))
            text = path.read_text()
            self.assertTrue(text.startswith("# header line\n# second header line\n"))
            self.assertIn("comment line(s) that this save will drop", err.getvalue())
            self.assertIn("python3-ruamel.yaml", err.getvalue())


if __name__ == "__main__":
    unittest.main()
