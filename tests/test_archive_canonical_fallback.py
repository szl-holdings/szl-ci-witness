"""Regression contracts for exact Git-object fallback after archive transforms."""
import hashlib
import importlib.util
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "source_census",
    Path(__file__).resolve().parents[1] / "tools/source_census.py",
)
census = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(census)


def blob(body: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()


class CanonicalFallbackTests(unittest.TestCase):
    def archive(self, body: bytes, canonical: bytes | None):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "source.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                member = tarfile.TarInfo("root/script.ps1")
                member.size = len(body)
                tar.addfile(member, io.BytesIO(body))
            expected_body = b"Write-Host 'ok'\n"
            entries = [{
                "path": "script.ps1",
                "sha": blob(expected_body),
                "size": len(expected_body),
                "mode": "100644",
                "type": "blob",
            }]
            loader = None if canonical is None else lambda _: canonical
            return census.scan_archive(archive, entries, loader)

    def test_export_line_ending_transform_uses_canonical_git_blob(self):
        canonical = b"Write-Host 'ok'\n"
        exported = canonical.replace(b"\n", b"\r\n")
        rows, errors = self.archive(exported, canonical)
        self.assertEqual(errors, [])
        self.assertTrue(rows[0]["content_verified"])
        self.assertTrue(rows[0]["archive_export_transformed"])
        self.assertEqual(rows[0]["sha256"], hashlib.sha256(canonical).hexdigest())
        self.assertNotEqual(rows[0]["archive_sha256"], rows[0]["sha256"])

    def test_mismatch_without_canonical_blob_stays_incomplete(self):
        canonical = b"Write-Host 'ok'\n"
        exported = canonical.replace(b"\n", b"\r\n")
        rows, errors = self.archive(exported, None)
        self.assertFalse(rows[0]["content_verified"])
        self.assertIn("archive content differs from Git blob: script.ps1", errors)
        self.assertIn("unverified blob: script.ps1", errors)

    def test_wrong_fallback_bytes_do_not_clear_mismatch(self):
        canonical = b"Write-Host 'ok'\n"
        exported = canonical.replace(b"\n", b"\r\n")
        rows, errors = self.archive(exported, b"wrong\n")
        self.assertFalse(rows[0]["content_verified"])
        self.assertEqual(rows[0]["canonical_fallback_error"], "GitObjectMismatch")
        self.assertIn("archive content differs from Git blob: script.ps1", errors)


if __name__ == "__main__":
    unittest.main(verbosity=2)
