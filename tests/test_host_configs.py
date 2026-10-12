import os
import unittest
from pathlib import Path

import yaml

import capture_inventory as ci

HOSTS = Path(__file__).resolve().parent.parent / "hosts"
# hardware_library checkout for the part_id cross-check; skipped when absent.
HARDWARE_LIBRARY = Path(os.environ.get("PANCAKEBATTER_HARDWARE_LIBRARY", Path.home() / "hardware_library"))


def part_ids(config):
    """(where, category, part_id, model) for every part_id on cameras, their lenses and bound projectors."""
    for key, cam in (config.get("cameras") or {}).items():
        if cam.get("part_id"):
            yield f"cameras.{key}", "camera", cam["part_id"], cam.get("model")
        lens = cam.get("lens") or {}
        if lens.get("part_id"):
            yield f"cameras.{key}.lens", "lens", lens["part_id"], lens.get("model")
    for i, rig in enumerate(config.get("rigs") or []):
        for j, out in enumerate(rig.get("display_outputs") or []):
            proj = out.get("projector") or {}
            if proj.get("part_id"):
                yield f"rigs[{i}].display_outputs[{j}].projector", "projector", proj["part_id"], proj.get("model")


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


class PartIdTests(unittest.TestCase):
    CONFIGS = {p.relative_to(HOSTS.parent): yaml.safe_load(p.read_text()) for p in sorted(HOSTS.glob("*/config.yml"))}

    def test_part_ids_name_the_right_category(self):
        for path, config in self.CONFIGS.items():
            for where, category, part_id, _ in part_ids(config):
                with self.subTest(path=path, where=where):
                    self.assertRegex(part_id, rf"^{category}\.[a-z0-9_]+(\.[a-z0-9_]+)+$")

    def test_part_ids_exist_in_hardware_library_and_match_the_model(self):
        if not (HARDWARE_LIBRARY / "parts").is_dir():
            self.skipTest(f"no hardware_library at {HARDWARE_LIBRARY}")
        parts = {p.parent.name: yaml.safe_load(p.read_text()) for p in HARDWARE_LIBRARY.glob("parts/*/*/part.yml")}
        for path, config in self.CONFIGS.items():
            for where, category, part_id, model in part_ids(config):
                with self.subTest(path=path, where=where):
                    self.assertIn(part_id, parts, f"{part_id} is not in {HARDWARE_LIBRARY}/parts")
                    self.assertEqual(parts[part_id].get("category"), category)
                    models = (parts[part_id].get("links") or {}).get("pancakebatter_models")
                    if models and model:
                        self.assertIn(model, models, f"{where} model {model!r} is not one of {part_id}'s {models}")


if __name__ == "__main__":
    unittest.main()
