"""Regression contracts for the public census inventory boundary."""
import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "source_census",
    Path(__file__).resolve().parents[1] / "tools/source_census.py",
)
census = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(census)


class InventoryIdentityTests(unittest.TestCase):
    def row(self, **overrides):
        base = {
            "id": 7,
            "full_name": "szl-holdings/example",
            "default_branch": "main",
            "archived": False,
            "updated_at": "2026-09-01T00:00:00Z",
        }
        base.update(overrides)
        return base

    def test_default_branch_drift_changes_inventory_identity(self):
        before = census.inventory_identity([self.row()])
        after = census.inventory_identity([self.row(default_branch="release")])
        self.assertNotEqual(before, after)

    def test_archive_state_drift_changes_inventory_identity(self):
        before = census.inventory_identity([self.row()])
        after = census.inventory_identity([self.row(archived=True)])
        self.assertNotEqual(before, after)

    def test_repository_identity_drift_changes_inventory_identity(self):
        before = census.inventory_identity([self.row()])
        after = census.inventory_identity([self.row(full_name="szl-holdings/renamed")])
        self.assertNotEqual(before, after)

    def test_incidental_timestamp_does_not_change_inventory_identity(self):
        before = census.inventory_identity([self.row(updated_at="2026-09-01T00:00:00Z")])
        after = census.inventory_identity([self.row(updated_at="2026-09-22T20:00:00Z")])
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
