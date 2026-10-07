"""The host config as an interface: stable keys resolve, consumers only use stable keys."""
import json
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
STABLE = json.loads((REPO / "schemas" / "system_config.v1.stable_keys.json").read_text())
CONSUMERS = sorted((REPO / "schemas" / "consumers").glob("*.json"))
HOST_CONFIGS = {p.parent.name: yaml.safe_load(p.read_text()) for p in sorted((REPO / "hosts").glob("*/config.yml"))}


def resolve(doc, path):
    """All values at a dotted path; '*' fans out over list elements or map values. Returns (found, values)."""
    values = [doc]
    for part in path.split("."):
        nxt = []
        for v in values:
            if part == "*":
                if isinstance(v, dict):
                    nxt.extend(v.values())
                elif isinstance(v, list):
                    nxt.extend(v)
            elif isinstance(v, dict) and part in v:
                nxt.append(v[part])
        values = nxt
        if not values:
            return False, []
    return True, values


class StableKeyTests(unittest.TestCase):
    def test_required_stable_keys_resolve_on_every_host_that_has_the_section(self):
        for host, cfg in HOST_CONFIGS.items():
            for key in STABLE["keys"]:
                section = key.split(".")[0]
                if section in STABLE["optional_sections"] and section not in cfg:
                    continue
                if key in STABLE["optional_keys"]:
                    continue
                with self.subTest(host=host, key=key):
                    self.assertTrue(resolve(cfg, key)[0], f"{key} missing on {host}")

    def test_optional_stable_keys_are_a_subset_and_each_resolves_somewhere_or_is_new(self):
        self.assertTrue(set(STABLE["optional_keys"]) <= set(STABLE["keys"]))
        never = [k for k in STABLE["optional_keys"] if not any(resolve(c, k)[0] for c in HOST_CONFIGS.values())]
        self.assertEqual(never, ["pdus.*.outlets.*.camera_serial"], never)   # promised, not yet populated anywhere

    def test_stable_key_list_is_well_formed(self):
        self.assertEqual(STABLE["schema"], {"name": "system_config", "version": 1})
        self.assertEqual(len(STABLE["keys"]), len(set(STABLE["keys"])))


class ConsumerTests(unittest.TestCase):
    def test_there_is_at_least_one_consumer_file(self):
        self.assertTrue(CONSUMERS)

    def test_consumers_declare_only_stable_keys_of_the_current_version(self):
        for path in CONSUMERS:
            decl = json.loads(path.read_text())
            with self.subTest(consumer=path.name):
                for field in ("consumer", "repo", "schema_version", "keys"):
                    self.assertIn(field, decl)
                self.assertEqual(decl["schema_version"], STABLE["schema"]["version"])
                self.assertTrue(decl["keys"])
                unstable = sorted(set(decl["keys"]) - set(STABLE["keys"]))
                self.assertEqual(unstable, [], f"{path.name} relies on keys that are not promised: {unstable}")

    def test_consumer_keys_resolve_where_the_section_exists(self):
        for path in CONSUMERS:
            decl = json.loads(path.read_text())
            for host, cfg in HOST_CONFIGS.items():
                for key in decl["keys"]:
                    section = key.split(".")[0]
                    if (section in STABLE["optional_sections"] and section not in cfg) or key in STABLE["optional_keys"]:
                        continue
                    with self.subTest(consumer=path.name, host=host, key=key):
                        self.assertTrue(resolve(cfg, key)[0])


class ResolveHelperTests(unittest.TestCase):
    def test_star_fans_out_over_lists_and_maps(self):
        doc = {"nics": [{"a": {"role": "camera"}}, {"b": {"role": "spare"}}], "cameras": {"m1": {"serial_number": "1"}}}
        self.assertEqual(resolve(doc, "nics.*.*.role"), (True, ["camera", "spare"]))
        self.assertEqual(resolve(doc, "cameras.*.serial_number"), (True, ["1"]))
        self.assertEqual(resolve(doc, "cameras.*.lens"), (False, []))


if __name__ == "__main__":
    unittest.main()
