import os
import sqlite3
import tempfile
import unittest

from doe_collector.collector import (
    CollectionError,
    classify_dimension_table,
    extract_report_date,
)
from doe_collector.storage import SnapshotValidationError, save_snapshot


def valid_tables():
    countries = [[f"country-{i}", "10"] for i in range(50)]
    def rows(prefix, size):
        return [[f"{prefix}-{i}", "1" if i < 500 else "0"] for i in range(size)]

    return {
        "01_destination_countries": (
            ["destination_country", "worker_count"],
            countries,
        ),
        "02_provinces": (["province", "worker_count"], rows("province", 77)),
        "03_districts": (["district", "worker_count"], rows("district", 500)),
        "04_job_titles": (["standard_job_title", "worker_count"], rows("job", 500)),
        "05_travel_methods": (["travel_method", "worker_count"], rows("travel", 8)),
        "06_education_levels": (["education_level", "worker_count"], rows("education", 10)),
        "07_embassy_labour_offices": (["embassy_labour_office", "worker_count"], rows("embassy", 11)),
        "08_gender": (["gender", "worker_count"], [["ชาย", "400"], ["หญิง", "100"]]),
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
            self.assertEqual(row[:3], (500, 50, 77))
            self.assertEqual(row[3], os.path.join("data", "snapshots", "1-ก-ย-2569"))
            self.assertFalse(os.path.isabs(row[3]))

    def test_all_base_dimensions_and_lossless_linked_rows_are_saved(self):
        with tempfile.TemporaryDirectory() as project_dir:
            db_path = os.path.join(project_dir, "monitor.db")
            tables = valid_tables()
            tables["linked_country_by_gender"] = (
                ["destination_country", "gender", "worker_count"],
                [["country-1", "ชาย", "8"], ["country-1", "ชาย", "8"]],
            )
            save_snapshot(
                "2026-09",
                tables,
                db_path=db_path,
                snapshots_dir=os.path.join(project_dir, "data", "snapshots"),
                current_dir=os.path.join(project_dir, "data", "current"),
            )
            conn = sqlite3.connect(db_path)
            table_counts = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "monthly_district_stats",
                    "monthly_education_stats",
                    "monthly_embassy_stats",
                    "monthly_gender_stats",
                    "monthly_country_gender_rows",
                )
            }
            quality = conn.execute(
                """SELECT row_count, distinct_key_count, status
                FROM snapshot_dataset_quality
                WHERE dataset_name = 'linked_country_by_gender'"""
            ).fetchone()
            conn.close()
            self.assertEqual(table_counts["monthly_district_stats"], 500)
            self.assertEqual(table_counts["monthly_education_stats"], 10)
            self.assertEqual(table_counts["monthly_embassy_stats"], 11)
            self.assertEqual(table_counts["monthly_gender_stats"], 2)
            self.assertEqual(table_counts["monthly_country_gender_rows"], 2)
            self.assertEqual(quality, (2, 1, "invalid"))

    def test_forced_snapshot_removes_stale_csv_files(self):
        with tempfile.TemporaryDirectory() as project_dir:
            current_dir = os.path.join(project_dir, "data", "current")
            os.makedirs(current_dir)
            stale_file = os.path.join(current_dir, "stale.csv")
            with open(stale_file, "w", encoding="utf-8") as handle:
                handle.write("old,data\n")
            save_snapshot(
                "2026-09",
                valid_tables(),
                db_path=os.path.join(project_dir, "monitor.db"),
                snapshots_dir=os.path.join(project_dir, "data", "snapshots"),
                current_dir=current_dir,
            )
            self.assertFalse(os.path.exists(stale_file))

    def test_dimension_classifier_recognizes_all_previously_missing_tables(self):
        self.assertEqual(
            classify_dimension_table(["ชาย", "หญิง"])[0],
            "08_gender",
        )
        self.assertEqual(
            classify_dimension_table(["มัธยมศึกษาตอนต้น", "ปริญญาตรี"])[0],
            "06_education_levels",
        )
        self.assertEqual(
            classify_dimension_table(["สนร.ไทเป", "สถานเอกอัครราชทูตไทย"])[0],
            "07_embassy_labour_offices",
        )
        districts = [f"อำเภอทดสอบ-{i}" for i in range(500)]
        self.assertEqual(classify_dimension_table(districts)[0], "03_districts")

    def test_report_date_fallback_is_not_allowed(self):
        with self.assertRaises(CollectionError):
            extract_report_date("Dashboard failed to render")
        self.assertEqual(extract_report_date("ข้อมูล ณ 1.ก.ย.2569"), "1.ก.ย.2569")
        self.assertEqual(extract_report_date("ข้อมูล ณ 1 กันยายน 2569"), "1 กันยายน 2569")
        self.assertEqual(
            extract_report_date("Data Last Updated: 9/1/2026 4:37:53 PM"),
            "2026-09",
        )


if __name__ == "__main__":
    unittest.main()
