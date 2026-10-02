import unittest
from pathlib import Path

import yaml

import capture_inventory as ci

HOSTS = Path(__file__).resolve().parent.parent / "hosts"


class HostConfigTests(unittest.TestCase):
    def test_every_host_config_validates_and_pcie_ids_are_strings(self):
        configs = sorted(HOSTS.glob("*/config*.yml"))
        self.assertTrue(configs)
        for path in configs:
            with self.subTest(path=path.relative_to(HOSTS.parent)):
                config = yaml.safe_load(path.read_text())
                self.assertEqual(ci.validate(config), [])
                for nic in config["nics"]:
                    pcie = next(iter(nic.values())).get("pcie_id")
                    # Unquoted 61:00.0 loads as a base-60 float under YAML 1.1.
                    self.assertTrue(pcie is None or isinstance(pcie, str), pcie)


if __name__ == "__main__":
    unittest.main()
