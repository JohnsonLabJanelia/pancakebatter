import base64
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

import recording_transfer_install as rti

REAL_WHEEL = Path("/home/jeremy/recording-transfer-wheels/citrus_recording_transfer-3.0.0-py3-none-any.whl")


def digest(data):
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def make_wheel(path, version="9.9.9", tamper=False, purelib=True):
    files = {
        "demo_pkg/__init__.py": b"VERSION = 'x'\n",
        "demo_pkg/cli.py": b"def main():\n    print('demo ok')\n    return 0\n",
        f"demo_pkg-{version}.dist-info/METADATA": f"Metadata-Version: 2.1\nName: demo-pkg\nVersion: {version}\n".encode(),
        f"demo_pkg-{version}.dist-info/WHEEL": f"Wheel-Version: 1.0\nRoot-Is-Purelib: {'true' if purelib else 'false'}\n".encode(),
        f"demo_pkg-{version}.dist-info/entry_points.txt": b"[console_scripts]\ndemo-cli = demo_pkg.cli:main\n",
    }
    record = "".join(f"{n},{digest(d)},{len(d)}\n" for n, d in files.items()) + f"demo_pkg-{version}.dist-info/RECORD,,\n"
    if tamper:
        files["demo_pkg/cli.py"] = b"def main():\n    return 1\n"
    with zipfile.ZipFile(path, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
        z.writestr(f"demo_pkg-{version}.dist-info/RECORD", record)
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.root = self.dir / "root"
        self.wheel = self.dir / "demo_pkg-9.9.9-py3-none-any.whl"
        sha = make_wheel(self.wheel)
        self.manifest = {"releases": [{"version": "9.9.9", "package": "demo-pkg", "wheel": self.wheel.name, "sha256": sha,
                                       "source": {"repo": "x/demo", "tag": "v9.9.9", "commit": "abc"}}]}

    def test_install_runs_and_verifies(self):
        final = rti.install("9.9.9", self.wheel, self.root, self.manifest)
        out = subprocess.run([str(final / "bin" / "demo-cli")], capture_output=True, text=True)
        self.assertEqual((out.returncode, out.stdout.strip()), (0, "demo ok"))
        info = json.loads((final / "INSTALLED.json").read_text())
        self.assertEqual((info["version"], info["wheel_sha256"], info["console_scripts"]), ("9.9.9", self.manifest["releases"][0]["sha256"], ["demo-cli"]))
        self.assertEqual(rti.verify_install(self.root, "9.9.9", self.manifest), [])
        self.assertEqual(rti.installed_versions(self.root), ["9.9.9"])
        self.assertFalse(list(final.glob("bin/activate*")))
        self.assertFalse([p for p in self.root.iterdir() if p.name.startswith(".")])   # no staging left behind

    def test_wrong_sha_unknown_version_and_existing_install_are_refused(self):
        bad = dict(self.manifest["releases"][0], sha256="0" * 64)
        with self.assertRaises(rti.InstallError):
            rti.install("9.9.9", self.wheel, self.root, {"releases": [bad]})
        with self.assertRaises(rti.InstallError):
            rti.install("1.0.0", self.wheel, self.root, self.manifest)
        rti.install("9.9.9", self.wheel, self.root, self.manifest)
        with self.assertRaises(rti.InstallError):
            rti.install("9.9.9", self.wheel, self.root, self.manifest)

    def test_record_mismatch_and_platform_wheels_are_refused(self):
        tampered = self.dir / "t.whl"
        sha = make_wheel(tampered, tamper=True)
        with self.assertRaises(rti.InstallError) as ctx:
            rti.install("9.9.9", tampered, self.root, {"releases": [dict(self.manifest["releases"][0], sha256=sha)]})
        self.assertIn("RECORD", str(ctx.exception))
        native = self.dir / "n.whl"
        sha = make_wheel(native, purelib=False)
        with self.assertRaises(rti.InstallError):
            rti.install("9.9.9", native, self.root, {"releases": [dict(self.manifest["releases"][0], sha256=sha)]})
        self.assertFalse(self.root.exists() and list(self.root.iterdir()))

    def test_verify_catches_a_modified_file_and_uninstall_removes(self):
        final = rti.install("9.9.9", self.wheel, self.root, self.manifest)
        target = next(final.glob("lib/python3*/site-packages/demo_pkg/cli.py"))
        target.chmod(0o644)
        target.write_text("def main():\n    return 7\n")
        self.assertTrue(any("modified after install" in p for p in rti.verify_install(self.root, "9.9.9", self.manifest)))
        rti.uninstall("9.9.9", self.root)
        self.assertEqual(rti.installed_versions(self.root), [])


@unittest.skipUnless(REAL_WHEEL.exists(), "3.0.0 wheel not present")
class RealWheelTests(unittest.TestCase):
    def test_repo_manifest_installs_the_real_3_0_0_wheel(self):
        manifest = rti.load_manifest()
        with tempfile.TemporaryDirectory() as d:
            final = rti.install("3.0.0", REAL_WHEEL, Path(d), manifest)
            out = subprocess.run([str(final / "bin" / "citrus-recording-transfer"), "--help"], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("--check-structure-only", out.stdout)
            self.assertEqual(rti.verify_install(Path(d), "3.0.0", manifest), [])


if __name__ == "__main__":
    unittest.main()
