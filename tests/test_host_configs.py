import os
import unittest
from pathlib import Path

import yaml

import capture_inventory as ci

HOSTS = Path(__file__).resolve().parent.parent / "hosts"
# hardware_library checkout for the part_id cross-check; skipped when absent.
HARDWARE_LIBRARY = Path(os.environ.get("PANCAKEBATTER_HARDWARE_LIBRARY", Path.home() / "hardware_library"))


SHARED = Path(__file__).resolve().parent.parent / "equipment" / "shared.yml"
# Instrument sections: (section, category of their part_id).
INSTRUMENT_SECTIONS = (("daqs", "daq"), ("photodetectors", "photodetector"), ("light_arrays", "light_source"))


def part_ids(config):
    """(where, category, part_id, model) for every part_id on cameras, lenses, instruments and filters."""
    for key, cam in (config.get("cameras") or {}).items():
        if cam.get("part_id"):
            yield f"cameras.{key}", "camera", cam["part_id"], cam.get("model")
        lens = cam.get("lens") or {}
        if lens.get("part_id"):
            yield f"cameras.{key}.lens", "lens", lens["part_id"], lens.get("model")
    for section, category in INSTRUMENT_SECTIONS:
        for entry in config.get(section) or []:
            where = f"{section}.{entry.get('name')}"
            if entry.get("part_id"):
                yield where, category, entry["part_id"], None
            for sub in ("filters", "calibration"):
                stack = (entry.get("calibration") or {}).get("filters") if sub == "calibration" else entry.get("filters")
                for i, flt in enumerate(stack if isinstance(stack, list) else []):
                    if isinstance(flt, dict) and flt.get("part_id"):
                        yield f"{where}.{sub}[{i}]", "filter", flt["part_id"], None


def shared_part_ids(doc):
    """(where, category, part_id, None) for equipment/shared.yml; the category is the id's first segment."""
    for item in doc.get("items") or []:
        if item.get("part_id"):
            yield f"items.{item.get('id')}", item["part_id"].split(".")[0], item["part_id"], None
        stack = (item.get("calibration") or {}).get("filters")
        for i, flt in enumerate(stack if isinstance(stack, list) else []):
            if isinstance(flt, dict) and flt.get("part_id"):
                yield f"items.{item.get('id')}.calibration[{i}]", "filter", flt["part_id"], None


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

    def all_part_ids(self):
        for path, config in self.CONFIGS.items():
            for found in part_ids(config):
                yield path, found
        if SHARED.is_file():
            for found in shared_part_ids(yaml.safe_load(SHARED.read_text()) or {}):
                yield SHARED.relative_to(HOSTS.parent), found

    def test_part_ids_name_the_right_category(self):
        for path, (where, category, part_id, _) in self.all_part_ids():
            with self.subTest(path=path, where=where):
                self.assertRegex(part_id, rf"^{category}\.[a-z0-9_]+(\.[a-z0-9_]+)+$")

    def test_part_ids_exist_in_hardware_library_and_match_the_model(self):
        if not (HARDWARE_LIBRARY / "parts").is_dir():
            self.skipTest(f"no hardware_library at {HARDWARE_LIBRARY}")
        parts = {p.parent.name: yaml.safe_load(p.read_text()) for p in HARDWARE_LIBRARY.glob("parts/*/*/part.yml")}
        for path, (where, category, part_id, model) in self.all_part_ids():
            with self.subTest(path=path, where=where):
                self.assertIn(part_id, parts, f"{part_id} is not in {HARDWARE_LIBRARY}/parts")
                self.assertEqual(parts[part_id].get("category"), category)
                models = (parts[part_id].get("links") or {}).get("pancakebatter_models")
                if models and model:
                    self.assertIn(model, models, f"{where} model {model!r} is not one of {part_id}'s {models}")

    def test_instrument_part_ids_are_collected(self):
        config = {"photodetectors": [{"name": "pd", "part_id": "photodetector.thorlabs.pda36a2",
                                      "filters": [{"part_id": "filter.thorlabs.fesh0750", "serial_or_lot": "L1"},
                                                  {"part_id": "filter.thorlabs.fgs900", "serial_or_lot": "K1"}],
                                      "calibration": {"filters": [{"part_id": "filter.thorlabs.fesh0750",
                                                                   "serial_or_lot": "L1"}]}}],
                  "light_arrays": [{"name": "la"}]}
        self.assertEqual([(w, c, p) for w, c, p, _ in part_ids(config)],
                         [("photodetectors.pd", "photodetector", "photodetector.thorlabs.pda36a2"),
                          ("photodetectors.pd.filters[0]", "filter", "filter.thorlabs.fesh0750"),
                          ("photodetectors.pd.filters[1]", "filter", "filter.thorlabs.fgs900"),
                          ("photodetectors.pd.calibration[0]", "filter", "filter.thorlabs.fesh0750")])


if __name__ == "__main__":
    unittest.main()
