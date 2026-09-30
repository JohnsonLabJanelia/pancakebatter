import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import nvidia_driver_upgrade as cli


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(cli.DEFAULT_MANIFEST.read_text())

    def test_manifest_rejects_config_injection(self):
        for section, field, value in [("target", "package_version", "610\nPin-Priority: 1001"),
                                      ("expected", "kernel", "--help"),
                                      ("target", "pin_package", "--allow-unauthenticated")]:
            bad = copy.deepcopy(self.manifest)
            bad[section][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                cli.validate_manifest(bad)

    def test_no_mutation_modes(self):
        for flag in ("--apply", "--install", "--execute", "--refresh-metadata"):
            with self.subTest(flag=flag), self.assertRaises(SystemExit) as exit:
                cli.main([flag])
            self.assertEqual(exit.exception.code, 2)

    def test_existing_output_refused_before_collectors(self):
        with tempfile.TemporaryDirectory() as root, patch.object(cli, "collect") as collect:
            with self.assertRaises(SystemExit):
                cli.main(["--output-dir", root])
            collect.assert_not_called()

    def test_symlink_and_apt_config_unsafe_output_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as root, patch.object(cli, "collect") as collect:
            link = Path(root) / "link"
            link.symlink_to(root, target_is_directory=True)
            for path in (link / "new", Path(root) / 'unsafe"path'):
                with self.subTest(path=str(path)), self.assertRaises(SystemExit):
                    cli.main(["--output-dir", str(path)])
            collect.assert_not_called()

    def test_collector_failure_is_not_installation_readiness(self):
        healthy = {"observations": {}, "blockers": [], "warnings": []}
        with patch.object(cli, "audit_host", side_effect=PermissionError("devices")), \
                patch.object(cli, "audit_recovery", return_value=healthy), \
                patch.object(cli, "build_apt_plan", return_value=healthy):
            report = cli.collect(self.manifest, Path("/unused"))
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["installation_ready"])
        self.assertEqual(report["blockers"][0]["section"], "host")

    def test_resolved_plan_still_needs_runtime_review(self):
        healthy = {"observations": {}, "blockers": [], "warnings": []}
        with patch.object(cli, "audit_host", return_value=healthy), \
                patch.object(cli, "audit_recovery", return_value=healthy), \
                patch.object(cli, "build_apt_plan", return_value=healthy):
            report = cli.collect(self.manifest, Path("/unused"))
        self.assertEqual(report["status"], "review_required")
        self.assertFalse(report["installation_ready"])

    def test_saved_report_has_manifest_checksum_and_private_directory(self):
        report = {"status": "blocked", "installation_ready": False, "sections": {},
                  "blockers": [{"section": "host", "code": "test", "message": "test blocker"}],
                  "warnings": [], "qualification": "Runtime qualification required."}
        with tempfile.TemporaryDirectory() as root, patch.object(cli, "collect", return_value=report):
            output = Path(root) / "new"
            result = cli.main(["--output-dir", str(output)])
            saved = json.loads((output / "report.json").read_text())
            self.assertEqual(result, 2)
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            self.assertEqual(saved["manifest_sha256"], hashlib.sha256(cli.DEFAULT_MANIFEST.read_bytes()).hexdigest())
            self.assertTrue((output / "SHA256SUMS").is_file())

    def test_recovery_checks_metadata_not_image_payload(self):
        with tempfile.TemporaryDirectory() as root:
            image = Path(root) / "image"
            image.mkdir()
            recovery = self.manifest["recovery"]
            recovery.update(backup_image=str(image), session_recovery=root,
                            rollback_installer=str(Path(root) / "rollback.run"))
            disk = recovery["source_disk"]
            for name in ("efi-nvram.dat", f"{disk}-gpt-1st", f"{disk}-gpt-2nd", f"{disk}-mbr",
                         f"{disk}p1.vfat-ptcl-img.zst", f"{disk}p2.ext4-ptcl-img.zst"):
                (image / name).write_bytes(b"metadata-only-test")
            (image / "disk").write_text(disk)
            (image / "parts").write_text(f"{disk}p1 {disk}p2")
            (image / "clonezilla-img").write_text(recovery["source_disk_serial"])
            mount = subprocess.CompletedProcess([], 0, recovery["backup_filesystem_uuid"], "")
            with patch.object(cli.subprocess, "run", return_value=mount), patch.object(cli, "sha256") as digest:
                result = cli.audit_recovery(self.manifest)
            digest.assert_not_called()
            self.assertEqual([x["code"] for x in result["blockers"]], ["rollback_installer_missing"])
            self.assertFalse(result["observations"]["image_payload_verified_by_planner"])
            (image / "clonezilla-img").write_text("different drive")
            with patch.object(cli.subprocess, "run", return_value=mount):
                result = cli.audit_recovery(self.manifest)
            self.assertIn("recovery_serial_mismatch", [x["code"] for x in result["blockers"]])


if __name__ == "__main__":
    unittest.main()
