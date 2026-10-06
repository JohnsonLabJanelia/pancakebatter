import unittest

import check_kernel_tuning as ckt

ISOLATED = ckt.parse_cpulist("1,2,6,8,10,12,38,40,42,44")
RECORDED = [c for c in range(64) if c not in ISOLATED]


class CpuListTests(unittest.TestCase):
    def test_parse_cpulist(self):
        self.assertEqual(ckt.parse_cpulist("1,2,6-8"), {1, 2, 6, 7, 8})
        self.assertEqual(ckt.parse_cpulist(""), set())
        self.assertEqual(ckt.parse_cpulist(None), set())


class IrqVerdictTests(unittest.TestCase):
    def test_exact_match_passes(self):
        level, msg = ckt.irq_verdict("mlnx1_p1_25g", RECORDED, RECORDED, ISOLATED)
        self.assertEqual(level, "ok")
        self.assertNotIn("differs", msg)

    def test_a_different_set_that_avoids_isolated_cores_passes_with_a_note(self):
        live = [c for c in RECORDED if c != 39]          # what the 2026-10-06 boot produced on mlnx2_p3_25g
        level, msg = ckt.irq_verdict("mlnx2_p3_25g", live, RECORDED, ISOLATED)
        self.assertEqual(level, "ok")
        self.assertIn("differs from the recorded set (+[] -[39])", msg)

    def test_any_isolated_core_fails_and_names_it(self):
        level, msg = ckt.irq_verdict("mlnx1_p2_25g", RECORDED + [6, 44], RECORDED, ISOLATED)
        self.assertEqual(level, "fail")
        self.assertIn("[6, 44]", msg)

    def test_unreadable_or_missing_interrupts_warn(self):
        self.assertEqual(ckt.irq_verdict("p", None, RECORDED, ISOLATED)[0], "warn")
        self.assertEqual(ckt.irq_verdict("p", [], RECORDED, ISOLATED)[0], "warn")

    def test_no_recorded_list_is_fine(self):
        self.assertEqual(ckt.irq_verdict("p", [0, 3, 4], None, ISOLATED)[0], "ok")


SIBLINGS = {1: [33], 2: [34], 6: [38], 8: [40], 38: [6], 40: [8]}
ISO = {1, 2, 6, 8, 38, 40}
ROLES = {
    "1": {"role": "unassigned", "note": "no consumer"},
    "2": {"role": "acquisition", "consumer": "orange"},
    "6": {"role": "yolo", "consumer": "orange", "camera": "2010093"},
    "8": {"role": "yolo", "consumer": "orange", "camera": "2010094"},
    "38": {"role": "smt_sibling", "sibling_of": 6},
    "40": {"role": "smt_sibling", "sibling_of": 8},
}


def levels(findings, level):
    return [m for l, m in findings if l == level]


class CoreRoleTests(unittest.TestCase):
    def test_complete_map_passes_with_warnings_for_unassigned_and_half_isolated_cores(self):
        f = ckt.core_role_findings(ISO, ROLES, SIBLINGS)
        self.assertEqual(levels(f, "fail"), [])
        warns = levels(f, "warn")
        self.assertTrue(any(m.startswith("cpu 1 is isolated but unassigned") for m in warns))
        self.assertTrue(any("cpu 1 is isolated but its hyperthread sibling(s) [33] are not" in m for m in warns))
        self.assertTrue(any("cpu 2 is isolated but its hyperthread sibling(s) [34] are not" in m for m in warns))
        oks = levels(f, "ok")
        self.assertIn("cpu 6: yolo for orange camera 2010093", oks)
        self.assertIn("cpu 38: idle sibling of cpu 6 (yolo)", oks)

    def test_isolated_cpu_without_an_entry_and_entry_for_a_shared_cpu_both_fail(self):
        roles = {k: v for k, v in ROLES.items() if k != "8"}
        roles["20"] = {"role": "yolo"}
        fails = levels(ckt.core_role_findings(ISO, roles, SIBLINGS), "fail")
        self.assertIn("cpu 8 is isolated but has no core_roles entry", fails)
        self.assertIn("core_roles lists cpu 20 (yolo), which is not isolated", fails)
        self.assertTrue(any("cpu 40 is the sibling of cpu 8" in m for m in fails))   # its owner lost its role

    def test_wrong_sibling_and_unknown_role_fail(self):
        roles = dict(ROLES, **{"38": {"role": "smt_sibling", "sibling_of": 8}, "2": {"role": "gaming"}})
        fails = levels(ckt.core_role_findings(ISO, roles, SIBLINGS), "fail")
        self.assertTrue(any("cpu 38 says sibling_of 8" in m for m in fails))
        self.assertTrue(any("cpu 2 has unknown role 'gaming'" in m for m in fails))

    def test_missing_map_is_one_warning(self):
        self.assertEqual(ckt.core_role_findings(ISO, None, SIBLINGS),
                         [("warn", "no kernel_tuning.core_roles recorded: nothing says what each isolated CPU is for")])

    def test_fully_isolated_cores_raise_no_sibling_warning(self):
        iso = {6, 8, 38, 40}
        roles = {k: v for k, v in ROLES.items() if k in ("6", "8", "38", "40")}
        self.assertEqual(levels(ckt.core_role_findings(iso, roles, SIBLINGS), "warn"), [])

    def test_pancake0_config_core_roles_cover_its_isolated_set(self):
        import yaml
        from pathlib import Path
        kt = yaml.safe_load((Path(ckt.SCRIPT_DIR) / "hosts" / "pancake0" / "config.yml").read_text())["kernel_tuning"]
        iso = ckt.parse_cpulist(kt["isolated_cores"])
        self.assertEqual({int(k) for k in kt["core_roles"]}, iso)
        self.assertTrue(all(e["role"] in ckt.CORE_ROLES for e in kt["core_roles"].values()))


if __name__ == "__main__":
    unittest.main()
