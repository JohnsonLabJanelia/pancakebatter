import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1]))
from nvidia_driver_prefetch import (  # noqa: E402
    _all_report_blockers,
    _parse_packages,
    _prepare_output,
    _select_record,
    _signed_index_digest,
    _verify_signed_index,
)


class MetadataTests(unittest.TestCase):
    def test_selects_amd64_and_ignores_i386(self):
        rows = _parse_packages("""Package: libnvidia-compute
Version: 610.57.04-1ubuntu1
Architecture: i386
Filename: ./i386.deb
Size: 1
SHA256: bad

Package: libnvidia-compute
Version: 610.57.04-1ubuntu1
Architecture: amd64
Filename: ./amd64.deb
Size: 2
SHA256: good
""")
        selected, blockers = _select_record(rows, "libnvidia-compute", "610.57.04-1ubuntu1")
        self.assertEqual([], blockers)
        self.assertEqual("amd64", selected["Architecture"])
        self.assertEqual("good", selected["SHA256"])

    def test_duplicate_amd64_metadata_is_rejected(self):
        stanza = """Package: p
Version: 1
Architecture: amd64
Filename: p.deb
Size: 1
SHA256: x
"""
        selected, blockers = _select_record(_parse_packages(stanza + "\n" + stanza), "p", "1")
        self.assertIsNone(selected)
        self.assertEqual("metadata_ambiguous", blockers[0]["code"])

    def test_signed_index_digest_and_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages = root / "Packages"
            packages.write_bytes(b"abc")
            digest = hashlib.sha256(b"abc").hexdigest()
            release = root / "InRelease"
            release.write_text(f"SHA256:\n {digest} 3 Packages\n-----BEGIN PGP SIGNATURE-----\n")
            self.assertEqual((digest, 3), _signed_index_digest(release.read_text(), "Packages"))
            self.assertEqual([], _verify_signed_index(release, packages, "Packages"))

    def test_signed_index_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Packages").write_bytes(b"abcd")
            (root / "InRelease").write_text("SHA256:\n " + "0" * 64 + " 4 Packages\n")
            blockers = _verify_signed_index(root / "InRelease", root / "Packages", "Packages")
            self.assertEqual("signed_index", blockers[0]["code"])


class GateTests(unittest.TestCase):
    def test_output_rejects_symlink_ancestry_and_config_delimiters(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            (parent / 'link').symlink_to(parent, target_is_directory=True)
            for output in (parent / 'link' / 'new', parent / 'bad"name'):
                with self.subTest(output=str(output)):
                    prepared, blockers = _prepare_output(output)
                    self.assertIsNone(prepared)
                    self.assertEqual('output_path', blockers[0]['code'])

    def test_nested_report_blockers_are_found(self):
        report = {"status": "review_required", "sections": {"apt": {"blockers": [{"code": "x"}]}}}
        self.assertEqual([{"code": "x"}], _all_report_blockers(report))

    def test_fresh_private_output_is_required(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            output = parent / "new"
            prepared, blockers = _prepare_output(output)
            self.assertEqual([], blockers)
            self.assertEqual(0o700, os.stat(prepared).st_mode & 0o777)
            _, blockers = _prepare_output(output)
            self.assertEqual("output_exists", blockers[0]["code"])


if __name__ == "__main__":
    unittest.main()
