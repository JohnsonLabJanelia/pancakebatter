"""Instrument sections (daqs, photodetectors, light_arrays) and equipment/shared.yml."""
import contextlib
import copy
import io
import json
import unittest
from datetime import date
from pathlib import Path

import yaml

import configio
import instruments

REPO = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 19)

try:
    import jsonschema  # noqa: F401
    HAVE_JSONSCHEMA = True
except ImportError:
    HAVE_JSONSCHEMA = False

# Ordered from the light side to the detector: shortpass, then KG glass.
FILTERS = [{"part_id": "filter.thorlabs.fesh0750", "serial_or_lot": "LOT-1", "fitted_at": "2026-10-01"},
           {"part_id": "filter.thorlabs.fgs900", "serial_or_lot": "KG-7", "fitted_at": "2026-10-01"}]
ND = {"part_id": "filter.thorlabs.ne10a", "serial_or_lot": None}
CALIBRATION = {"certificate_id": "C-1", "calibrated_at": "2026-10-02", "due": "2027-10-02",
               "performed_by": "Jeremy", "filters": [{"part_id": "filter.thorlabs.fesh0750", "serial_or_lot": "LOT-1"},
                                                     {"part_id": "filter.thorlabs.fgs900", "serial_or_lot": "KG-7"}]}


def good():
    """A minimal valid host config with one of everything, wired consistently."""
    return {
        "schema": {"name": "system_config", "version": 1},
        "system_info": {"hostname": "t", "network_renderer": "NetworkManager"},
        "nics": [{"mlnx1_p1_25g": {"altname": None, "role": "camera", "managed": True, "expected_link": True,
                                   "mac_address": "aa:bb:cc:dd:ee:01", "mtu": 9000, "ip_address": "192.168.110.1/24",
                                   "link_settings": {"speed": 25000, "autoneg": True}}}],
        "cameras": {"E0-55-97-1E-B6-BB": {"serial_number": 2010096, "ip_address": "192.168.110.2",
                                          "nic_port": "mlnx1_p1_25g"}},
        "pdus": [{"name": "pdu1", "ip_address": "192.168.20.177",
                  "outlets": {7: {"name": "light-array-1", "description": "Light array 1"}}}],
        "daqs": [{
            "name": "daq0", "part_id": "daq.mcc.usb_1608g", "serial_number": "01ABCDEF", "firmware": None,
            "driver": {"name": "uldaq", "version": "1.2.1"},
            "channels": [
                {"id": "AI0", "direction": "in", "kind": "analog", "signal": "corner_tl",
                 "source": {"kind": "photodetector", "name": "pd-tl"}},
                {"id": "DIO0", "direction": "in", "kind": "digital", "signal": "cam2010096_strobe",
                 "source": {"kind": "camera_gpio", "camera_serial": "2010096", "gpio_line": 1}},
                {"id": "DIO1", "direction": "in", "kind": "digital", "signal": "array1_power",
                 "source": {"kind": "light_array", "name": "light-array-1"}},
                {"id": "DIO2", "direction": "out", "kind": "digital", "signal": "host_sync",
                 "source": {"kind": "host"}},
                {"id": "CTR0", "direction": "in", "kind": "counter", "signal": "projector_vsync",
                 "source": {"kind": "other", "note": "projector VGA vsync tap"}},
            ],
            "calibration": {"certificate_id": None, "calibrated_at": "2026-10-01", "due": "2027-10-01",
                            "performed_by": "MCC"},
        }],
        "photodetectors": [{
            "name": "pd-tl", "part_id": "photodetector.thorlabs.pda36a2", "serial_number": "P1",
            "settings": {"gain_db": 40}, "filters": [dict(f) for f in FILTERS], "daq_channel": {"daq": "daq0", "channel": "AI0"},
            "calibration": copy.deepcopy(CALIBRATION),
        }],
        "light_arrays": [{
            "name": "light-array-1", "pdu": "pdu1", "outlet": 7, "wavelength_nm": 850, "driver": None,
            "strobe": {"source": "camera_gpio", "camera_serial": "2010096", "gpio_line": None, "via": "unknown"},
        }],
    }


def shared_doc(*items):
    return {"schema": {"name": "shared_equipment", "version": 1}, "items": list(items)}


def shared_pd(**overrides):
    item = {"id": "pd-cal-1", "part_id": "photodetector.thorlabs.pda36a2", "serial_number": "S1",
            "description": "calibration detector", "home_location": "storage", "current_location": "t",
            "calibration": copy.deepcopy(CALIBRATION)}
    item.update(overrides)
    return item


def fails(config, **kw):
    return [m for level, m in instruments.findings(config, today=TODAY, **kw) if level == "fail"]


def warns(config, **kw):
    return [m for level, m in instruments.findings(config, today=TODAY, **kw) if level == "warn"]


class SchemaTests(unittest.TestCase):
    def test_full_example_validates(self):
        self.assertEqual(configio.validate(good()), [])
        self.assertEqual(instruments.findings(good(), today=TODAY), [])

    def test_sections_are_optional(self):
        cfg = good()
        for section in ("daqs", "photodetectors", "light_arrays"):
            del cfg[section]
        self.assertEqual(configio.validate(cfg), [])

    def test_explicit_no_filter_is_accepted(self):
        cfg = good()
        cfg["photodetectors"][0]["filters"] = []
        cfg["photodetectors"][0]["calibration"]["filters"] = []
        self.assertEqual(configio.validate(cfg), [])

    def test_the_old_single_filter_key_is_an_error(self):
        for block in ("detector", "calibration"):
            cfg = good()
            pd = cfg["photodetectors"][0]
            target = pd if block == "detector" else pd["calibration"]
            target["filter"] = target.pop("filters")[0]
            with self.subTest(block=block):
                # With jsonschema, a calibration without filters fails the schema first and the repo checks never run.
                errors = configio.validate(cfg)
                self.assertTrue(any("'filter' is now 'filters'" in e or "'filters' is a required property" in e
                                    for e in errors), errors)

    @unittest.skipUnless(HAVE_JSONSCHEMA, "jsonschema not installed")
    def test_schema_rejects_bad_enums_and_missing_fields(self):
        for mutate, needle in (
            (lambda c: c["daqs"][0]["channels"][0].update(direction="sideways"), "direction"),
            (lambda c: c["light_arrays"][0]["strobe"].update(via="telepathy"), "via"),
            (lambda c: c["light_arrays"][0]["strobe"].pop("camera_serial"), "camera_serial"),
            (lambda c: c["photodetectors"][0]["calibration"].pop("filters"), "filters"),
            (lambda c: c["photodetectors"][0].update(filters="none"), "filters"),
            (lambda c: c["photodetectors"][0]["calibration"].update(due=date(2027, 1, 1)), "due"),
            (lambda c: c["photodetectors"][0].update(part_id="camera.thorlabs.pda36a2"), "part_id"),
            (lambda c: c["photodetectors"][0].pop("serial_number"), "photodetectors"),
        ):
            cfg = good()
            mutate(cfg)
            with self.subTest(needle=needle):
                errors = configio.validate(cfg)
                self.assertTrue(any(needle in e for e in errors), errors)


class CrossCheckTests(unittest.TestCase):
    def assertFails(self, cfg, needle):
        found = fails(cfg)
        self.assertTrue(any(needle in m for m in found), found)
        self.assertTrue(any(needle in e for e in configio.validate(cfg)))

    def test_daq_camera_source_must_be_a_known_camera(self):
        cfg = good()
        cfg["daqs"][0]["channels"][1]["source"]["camera_serial"] = "999"
        self.assertFails(cfg, "camera_serial '999' is not in cameras")

    def test_daq_photodetector_and_light_array_sources_must_exist(self):
        cfg = good()
        cfg["daqs"][0]["channels"][2]["source"]["name"] = "light-array-9"
        self.assertFails(cfg, "light array 'light-array-9' is not in light_arrays")
        cfg = good()
        cfg["daqs"][0]["channels"][0]["source"]["name"] = "pd-xx"
        self.assertFails(cfg, "photodetector 'pd-xx' is not in photodetectors")

    def test_other_source_needs_a_note_and_channels_are_unique(self):
        cfg = good()
        del cfg["daqs"][0]["channels"][4]["source"]["note"]
        self.assertTrue(any("needs a note" in m for m in fails(cfg)))
        cfg = good()
        cfg["daqs"][0]["channels"][1]["id"] = "AI0"
        self.assertTrue(any("listed twice" in m for m in fails(cfg)))

    def test_photodetector_daq_channel_must_exist_and_be_wired_to_it(self):
        cfg = good()
        cfg["photodetectors"][0]["daq_channel"]["daq"] = "daq9"
        self.assertFails(cfg, "daq 'daq9' is not in daqs")
        cfg = good()
        cfg["photodetectors"][0]["daq_channel"]["channel"] = "AI7"
        self.assertFails(cfg, "has no channel 'AI7'")
        cfg = good()
        cfg["photodetectors"][0]["daq_channel"]["channel"] = "DIO0"
        self.assertFails(cfg, "not this photodetector")

    def test_light_array_name_must_match_its_pdu_outlet(self):
        cfg = good()
        cfg["light_arrays"][0]["outlet"] = 8
        self.assertFails(cfg, "has no outlet 8")
        cfg = good()
        cfg["light_arrays"][0]["pdu"] = "pdu9"
        self.assertFails(cfg, "pdu 'pdu9' is not in pdus")
        cfg = good()
        cfg["pdus"][0]["outlets"][7]["name"] = "unused"
        self.assertFails(cfg, "must share a name")

    def test_outlet_may_be_written_as_a_string(self):
        cfg = good()
        cfg["light_arrays"][0]["outlet"] = "7"
        self.assertEqual(fails(cfg), [])

    def test_strobe_camera_must_be_a_known_camera(self):
        cfg = good()
        cfg["light_arrays"][0]["strobe"]["camera_serial"] = "2010093"
        self.assertFails(cfg, "camera_serial '2010093' is not in cameras")

    def test_strobe_from_a_daq_needs_a_real_channel(self):
        cfg = good()
        cfg["light_arrays"][0]["strobe"] = {"source": "daq", "daq_channel": {"daq": "daq0", "channel": "DIO9"},
                                            "via": "driver_board"}
        self.assertFails(cfg, "has no channel 'DIO9'")
        cfg["light_arrays"][0]["strobe"]["daq_channel"]["channel"] = "DIO2"
        self.assertEqual(configio.validate(cfg), [])

    def test_duplicate_names_fail(self):
        cfg = good()
        cfg["light_arrays"].append(copy.deepcopy(cfg["light_arrays"][0]))
        self.assertFails(cfg, "'light-array-1' is used twice")

    def test_checks_survive_a_malformed_config_without_jsonschema(self):
        cfg = good()
        cfg["daqs"][0]["channels"][0]["source"] = "photodetector"
        cfg["light_arrays"][0]["strobe"] = None
        cfg["photodetectors"][0]["daq_channel"] = ["daq0", "AI0"]
        self.assertTrue(fails(cfg))


class FilterAndCalibrationTests(unittest.TestCase):
    def test_a_different_element_invalidates_the_calibration_and_names_its_index(self):
        cfg = good()
        cfg["photodetectors"][0]["filters"][1]["serial_or_lot"] = "KG-8"
        found = fails(cfg)
        self.assertTrue(any("no longer applies" in m and "filters[1] is" in m and "KG-8" in m and "KG-7" in m
                            for m in found), found)
        self.assertTrue(configio.validate(cfg))

    def test_a_different_part_id_is_a_mismatch(self):
        cfg = good()
        cfg["photodetectors"][0]["filters"][0]["part_id"] = "filter.thorlabs.fesh0700"
        self.assertTrue(any("filters[0] is filter.thorlabs.fesh0700" in m for m in fails(cfg)))

    def test_an_added_or_removed_element_is_a_mismatch(self):
        for change, needle in ((lambda s: s.append(dict(ND)), "filters[2] filter.thorlabs.ne10a (no serial/lot) was added"),
                               (lambda s: s.pop(), "filters[1] filter.thorlabs.fgs900 (KG-7) is missing"),
                               (lambda s: s.insert(1, dict(ND)), "filters[1] is filter.thorlabs.ne10a")):
            cfg = good()
            change(cfg["photodetectors"][0]["filters"])
            with self.subTest(needle=needle):
                found = fails(cfg)
                self.assertTrue(any(needle in m and "no longer applies" in m for m in found), found)

    def test_a_different_order_is_a_mismatch(self):
        cfg = good()
        cfg["photodetectors"][0]["filters"].reverse()
        self.assertTrue(any("different order" in m for m in fails(cfg)))

    def test_removing_the_filters_or_not_recording_them_is_a_mismatch(self):
        for fitted in ([], None):
            cfg = good()
            if fitted is None:
                del cfg["photodetectors"][0]["filters"]
            else:
                cfg["photodetectors"][0]["filters"] = fitted
            with self.subTest(fitted=fitted):
                self.assertTrue(any("no longer applies" in m for m in fails(cfg)))

    def test_calibrating_with_a_stack_and_matching_it_with_the_nd_passes(self):
        cfg = good()
        cfg["photodetectors"][0]["filters"].append(dict(ND, fitted_at="2026-10-03"))
        cfg["photodetectors"][0]["calibration"]["filters"].append(dict(ND))
        self.assertEqual(fails(cfg), [])

    def test_fitted_at_does_not_take_part_in_the_comparison(self):
        cfg = good()
        cfg["photodetectors"][0]["filters"][0]["fitted_at"] = "2026-09-01"
        self.assertEqual(fails(cfg), [])

    def test_overdue_calibration_is_a_warning_not_an_error(self):
        cfg = good()
        cfg["photodetectors"][0]["calibration"]["due"] = "2026-10-18"
        cfg["daqs"][0]["calibration"]["due"] = "2026-01-01"
        self.assertEqual(fails(cfg), [])
        found = warns(cfg)
        self.assertEqual(len(found), 2, found)
        self.assertTrue(any("photodetectors.pd-tl: calibration C-1 was due 2026-10-18" in m for m in found), found)
        self.assertEqual(configio.validate(cfg), [])
        self.assertEqual(len(configio.warnings(cfg)), 2)

    def test_due_today_is_not_overdue(self):
        cfg = good()
        cfg["photodetectors"][0]["calibration"]["due"] = TODAY.isoformat()
        self.assertEqual(warns(cfg), [])

    def test_unparseable_dates_fail(self):
        cfg = good()
        cfg["daqs"][0]["calibration"]["due"] = "2026-13-40"
        self.assertTrue(any("calibration.due" in m for m in fails(cfg)))


class SharedEquipmentTests(unittest.TestCase):
    def test_repo_file_is_valid(self):
        doc = instruments.load_shared()
        self.assertEqual(instruments.validate_shared(doc, today=TODAY), ([], []))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(instruments.main([]), 0)

    def test_a_good_item_validates(self):
        self.assertEqual(instruments.validate_shared(shared_doc(shared_pd()), today=TODAY), ([], []))

    def test_duplicate_ids_and_missing_fields_fail(self):
        errors, _ = instruments.validate_shared(shared_doc(shared_pd(), shared_pd()), today=TODAY)
        self.assertTrue(any("used twice" in e for e in errors), errors)
        item = shared_pd()
        del item["current_location"]
        errors, _ = instruments.validate_shared(shared_doc(item), today=TODAY)
        self.assertTrue(any("current_location" in e for e in errors), errors)
        errors, _ = instruments.validate_shared({"schema": {"name": "other", "version": 1}, "items": []})
        self.assertTrue(errors)

    def test_overdue_shared_calibration_warns(self):
        item = shared_pd(calibration={**CALIBRATION, "due": "2026-06-01"})
        errors, warnings = instruments.validate_shared(shared_doc(item), today=TODAY)
        self.assertEqual(errors, [])
        self.assertTrue(any("items.pd-cal-1: calibration C-1 was due 2026-06-01" in w for w in warnings), warnings)

    def test_missing_file_reads_as_empty(self):
        doc = instruments.load_shared(REPO / "equipment" / "no-such-file.yml")
        self.assertEqual(instruments.validate_shared(doc), ([], []))

    def test_schema_copies_match_system_config(self):
        system = json.loads((REPO / "schemas" / "system_config.v1.schema.json").read_text())["$defs"]
        shared = json.loads((REPO / "schemas" / "shared_equipment.v1.schema.json").read_text())["$defs"]
        for name in ("calibration", "filterRef", "filterStack", "date"):
            with self.subTest(name=name):
                self.assertEqual(shared[name], system[name])


class SharedIdTests(unittest.TestCase):
    def config_with_shared_pd(self):
        cfg = good()
        pd = cfg["photodetectors"][0]
        for field in instruments.SHARED_FIELDS:
            del pd[field]
        pd["shared_id"] = "pd-cal-1"
        return cfg

    def test_shared_item_supplies_serial_and_calibration(self):
        cfg = self.config_with_shared_pd()
        shared = shared_doc(shared_pd())
        self.assertEqual(instruments.findings(cfg, shared, today=TODAY), [])
        resolved = instruments.resolve_shared(cfg["photodetectors"][0], instruments.shared_items(shared))
        self.assertEqual(resolved["serial_number"], "S1")
        self.assertEqual(resolved["calibration"]["certificate_id"], "C-1")
        self.assertEqual(resolved["name"], "pd-tl")

    @unittest.skipUnless(HAVE_JSONSCHEMA, "jsonschema not installed")
    def test_schema_accepts_shared_id_without_serial(self):
        errors = [e for e in configio.validate(self.config_with_shared_pd()) if "not an item" not in e]
        self.assertEqual(errors, [])

    def test_unknown_shared_id_fails(self):
        self.assertTrue(any("is not an item in equipment/shared.yml" in m
                            for m in fails(self.config_with_shared_pd(), shared=shared_doc())))

    def test_repeating_a_shared_field_fails(self):
        cfg = self.config_with_shared_pd()
        cfg["photodetectors"][0]["serial_number"] = "S1"
        self.assertTrue(any("serial_number comes from shared item" in m for m in fails(cfg, shared=shared_doc(shared_pd()))))

    def test_item_recorded_elsewhere_warns(self):
        found = warns(self.config_with_shared_pd(), shared=shared_doc(shared_pd(current_location="storage")))
        self.assertTrue(any("current_location 'storage'" in m for m in found), found)

    def test_filter_is_compared_with_the_shared_calibration(self):
        cfg = self.config_with_shared_pd()
        cfg["photodetectors"][0]["filters"][0]["part_id"] = "filter.thorlabs.fesh0700"
        self.assertTrue(any("no longer applies" in m for m in fails(cfg, shared=shared_doc(shared_pd()))))

    def test_shared_id_on_a_daq(self):
        cfg = good()
        daq = cfg["daqs"][0]
        for field in instruments.SHARED_FIELDS:
            del daq[field]
        daq["shared_id"] = "daq-loaner"
        item = {"id": "daq-loaner", "serial_number": "D1", "description": "loaner DAQ",
                "home_location": "storage", "current_location": "t"}
        self.assertEqual(instruments.findings(cfg, shared_doc(item), today=TODAY), [])


class Pancake0Tests(unittest.TestCase):
    CONFIG = yaml.safe_load((REPO / "hosts" / "pancake0" / "config.yml").read_text())

    def test_pancake0_validates_with_its_light_arrays(self):
        self.assertEqual(configio.validate(self.CONFIG), [])
        self.assertEqual(instruments.findings(self.CONFIG), [])
        arrays = {la["name"]: la for la in self.CONFIG["light_arrays"]}
        self.assertEqual(sorted(arrays), ["light-array-1", "light-array-2"])
        self.assertEqual({la["outlet"] for la in arrays.values()}, {7, 8})
        for la in arrays.values():
            self.assertEqual(la["strobe"]["source"], "camera_gpio")
            self.assertEqual(la["strobe"]["camera_serial"], "2010096")
            self.assertIsNone(la["strobe"]["gpio_line"])
            self.assertEqual(la["strobe"]["via"], "unknown")
            self.assertNotIn("part_id", la)

    def test_pancake0_has_no_daqs_or_photodetectors_yet(self):
        self.assertNotIn("daqs", self.CONFIG)
        self.assertNotIn("photodetectors", self.CONFIG)


if __name__ == "__main__":
    unittest.main()
