import os
import sqlite3
import tempfile
import unittest

from doe_collector.collector import CollectionError, extract_report_date
from doe_collector.storage import SnapshotValidationError, save_snapshot


def valid_tables():
    countries = [[f"country-{i}", "10"] for i in range(50)]
    provinces = [[f"province-{i}", "5"] for i in range(70)]
    return {
        "01_destination_countries": (
            ["destination_country", "worker_count"],
            countries,
        ),
        "02_provinces": (["province", "worker_count"], provinces),
    }


class SnapshotHealthGuardTests(unittest.TestCase):
    def test_empty_snapshot_is_rejected_without_creating_database(self):
        with tempfile.TemporaryDirectory() as project_dir:
            db_path = os.path.join(project_dir, "monitor.db")
            with self.assertRaises(SnapshotValidationError):
                save_snapshot(
                    "2026-09",
                    {},
                    db_path=db_path,
                    snapshots_dir=os.path.join(project_dir, "data", "snapshots"),
                    current_dir=os.path.join(project_dir, "data", "current"),
                )
            self.assertFalse(os.path.exists(db_path))

    def test_snapshot_path_is_portable_and_counts_are_saved(self):
        with tempfile.TemporaryDirectory() as project_dir:
            db_path = os.path.join(project_dir, "monitor.db")
            folder = os.path.join(project_dir, "data", "snapshots")
            save_snapshot(
                "1.ก.ย.2569",
                valid_tables(),
                db_path=db_path,
                snapshots_dir=folder,
                current_dir=os.path.join(project_dir, "data", "current"),
            )
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT total_workers, total_countries, total_provinces, snapshot_dir FROM snapshots"
            ).fetchone()
            conn.close()
            self.assertEqual(row[:3], (500, 50, 70))
            self.assertEqual(row[3], os.path.join("data", "snapshots", "1-ก-ย-2569"))
            self.assertFalse(os.path.isabs(row[3]))

    def test_report_date_fallback_is_not_allowed(self):
        with self.assertRaises(CollectionError):
            extract_report_date("Dashboard failed to render")
        self.assertEqual(extract_report_date("ข้อมูล ณ 1.ก.ย.2569"), "1.ก.ย.2569")
        self.assertEqual(extract_report_date("ข้อมูล ณ 1 กันยายน 2569"), "1 กันยายน 2569")


if __name__ == "__main__":
    unittest.main()
