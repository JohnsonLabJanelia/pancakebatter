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


if __name__ == "__main__":
    unittest.main()
