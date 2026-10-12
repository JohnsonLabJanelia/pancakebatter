"""libexec/ptp4l_gm_settings.sh against a fake pmc and copies of the tzdata leap-second table."""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "libexec" / "ptp4l_gm_settings.sh"
LEAP_LIST = Path("/usr/share/zoneinfo/leap-seconds.list")
NOW = 1_791_000_000          # 2026-10-03, inside the installed table's validity
NTP_MINUS_UNIX = 2_208_988_800

# Fake pmc: GET prints the settings in state.txt, SET rewrites state.txt from its arguments.
FAKE_PMC = """#!/usr/bin/env bash
state="$(dirname "$0")/state.txt"
cmd="${@: -1}"
case "$cmd" in
  "GET GRANDMASTER_SETTINGS_NP")
    echo "sending: GET GRANDMASTER_SETTINGS_NP"
    echo "	a088c2.fffe.69119e-0 seq 0 RESPONSE MANAGEMENT GRANDMASTER_SETTINGS_NP "
    sed 's/^/		/' "$state" ;;
  "SET GRANDMASTER_SETTINGS_NP "*)
    set -- ${cmd#SET GRANDMASTER_SETTINGS_NP }
    : > "$state"
    while (( $# >= 2 )); do echo "$1 $2" >> "$state"; shift 2; done ;;
esac
"""

SETTINGS = {
    "clockClass": "248", "clockAccuracy": "0xfe", "offsetScaledLogVariance": "0xffff",
    "currentUtcOffset": "37", "leap61": "0", "leap59": "0", "currentUtcOffsetValid": "0",
    "ptpTimescale": "1", "timeTraceable": "0", "frequencyTraceable": "0", "timeSource": "0xa0",
}


@unittest.skipUnless(LEAP_LIST.is_file(), "tzdata leap-seconds.list not installed")
class GmSettingsTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        pmc = self.dir / "pmc"
        pmc.write_text(FAKE_PMC)
        pmc.chmod(0o755)
        self.leap = self.dir / "leap-seconds.list"
        self.leap.write_text(LEAP_LIST.read_text())

    def run_script(self, offset="37", now=NOW):
        settings = dict(SETTINGS, currentUtcOffset=offset)
        (self.dir / "state.txt").write_text("".join(f"{k} {v}\n" for k, v in settings.items()))
        env = dict(os.environ, PTP4L_GM_PMC=str(self.dir / "pmc"),
                   PTP4L_GM_LEAP_SECONDS_LIST=str(self.leap), PTP4L_GM_NOW_UNIX=str(now))
        result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True,
                                timeout=60)
        state = dict(line.split() for line in (self.dir / "state.txt").read_text().splitlines())
        return result, state

    def test_declares_valid_when_table_matches(self):
        result, state = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["currentUtcOffsetValid"], "1")
        self.assertEqual({k: v for k, v in state.items() if k != "currentUtcOffsetValid"},
                         {k: v for k, v in SETTINGS.items() if k != "currentUtcOffsetValid"})
        self.assertIn("declared valid", result.stdout)

    def test_refuses_offset_the_table_does_not_give(self):
        result, state = self.run_script(offset="36")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["currentUtcOffsetValid"], "0")
        self.assertIn("expected 37", result.stderr)

    def test_refuses_expired_table(self):
        expires = int(next(l.split()[1] for l in self.leap.read_text().splitlines()
                           if l.startswith("#@"))) - NTP_MINUS_UNIX
        result, state = self.run_script(now=expires + 1)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["currentUtcOffsetValid"], "0")
        self.assertIn("expired", result.stderr)

    def test_refuses_edited_table(self):
        text = re.sub(r"^(3692217600\s+)37", r"\g<1>38", self.leap.read_text(), flags=re.M)
        self.assertNotEqual(text, self.leap.read_text())
        self.leap.write_text(text)
        result, state = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["currentUtcOffsetValid"], "0")
        self.assertIn("SHA-1", result.stderr)

    def test_warns_near_expiry(self):
        expires = int(next(l.split()[1] for l in self.leap.read_text().splitlines()
                           if l.startswith("#@"))) - NTP_MINUS_UNIX
        result, state = self.run_script(now=expires - 30 * 86400)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["currentUtcOffsetValid"], "1")
        self.assertIn("warning", result.stderr)


if __name__ == "__main__":
    unittest.main()
