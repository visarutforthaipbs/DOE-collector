# -*- coding: utf-8 -*-
"""Database and snapshot storage engine for DOE Collector."""

import csv
import os
import sqlite3
from datetime import datetime

DEFAULT_PROJECT_DIR = os.path.dirname(os.path.dirname(__file__))
DEFAULT_DB_PATH = os.path.join(DEFAULT_PROJECT_DIR, "doe_labour_monitoring.db")
DEFAULT_SNAPSHOTS_DIR = os.path.join(DEFAULT_PROJECT_DIR, "data", "snapshots")
DEFAULT_CURRENT_DIR = os.path.join(DEFAULT_PROJECT_DIR, "data", "current")

BASE_DATASETS = {
    "01_destination_countries": ("monthly_country_stats", "country", 50),
    "02_provinces": ("monthly_province_stats", "province", 70),
    "03_districts": ("monthly_district_stats", "district", 500),
    "04_job_titles": ("monthly_job_stats", "job_title", 500),
    "05_travel_methods": ("monthly_travel_stats", "travel_method", 5),
    "06_education_levels": ("monthly_education_stats", "education_level", 5),
    "07_embassy_labour_offices": ("monthly_embassy_stats", "embassy_labour_office", 5),
    "08_gender": ("monthly_gender_stats", "gender", 2),
}

LINKED_DATASETS = {
    "linked_country_by_district": ("monthly_country_district_rows", "district"),
    "linked_country_by_education": ("monthly_country_education_rows", "education_level"),
    "linked_country_by_gender": ("monthly_country_gender_rows", "gender"),
    "linked_country_by_job": ("monthly_country_job_rows", "job_title"),
    "linked_country_by_province": ("monthly_country_province_rows", "province"),
    "linked_country_by_travel_method": ("monthly_country_travel_method_rows", "travel_method"),
}


class SnapshotValidationError(ValueError):
    """Raised when a scrape is incomplete and must not be persisted."""


def _parse_count(value):
    return int(str(value).replace(",", "").strip())


def _normalized_key(values):
    return tuple(str(value).strip() for value in values)


def validate_snapshot_tables(tables_dict):
    """Reject empty, malformed, or obviously partial base extractions."""
    problems = []
    for dataset_name, (_, _, minimum_rows) in BASE_DATASETS.items():
        rows = tables_dict.get(dataset_name, ([], []))[1]
        if len(rows) < minimum_rows:
            problems.append(
                f"{dataset_name} has {len(rows)} rows; expected at least {minimum_rows}"
            )
            continue
        valid_counts = []
        for row in rows:
            if len(row) < 2:
                continue
            try:
                count = _parse_count(row[1])
            except (TypeError, ValueError):
                continue
            if count >= 0:
                valid_counts.append(count)
        if len(valid_counts) != len(rows):
            problems.append(f"{dataset_name} contains invalid worker counts")
        elif dataset_name == "01_destination_countries" and sum(valid_counts) <= 0:
            problems.append("destination-country worker total is zero")
    if problems:
        raise SnapshotValidationError(
            "Snapshot validation failed: " + "; ".join(problems)
        )


def get_connection(db_path=DEFAULT_DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path=DEFAULT_DB_PATH):
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS snapshots (
        snapshot_id TEXT PRIMARY KEY,
        report_date TEXT NOT NULL,
        scraped_at TEXT NOT NULL,
        total_workers INTEGER NOT NULL,
        total_countries INTEGER NOT NULL,
        total_provinces INTEGER NOT NULL,
        snapshot_dir TEXT NOT NULL
    )
    """)
    for table_name, key_column, _ in BASE_DATASETS.values():
        cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            snapshot_id TEXT NOT NULL,
            report_date TEXT NOT NULL,
            {key_column} TEXT NOT NULL,
            worker_count INTEGER NOT NULL,
            PRIMARY KEY (snapshot_id, {key_column}),
            FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id)
        )
        """)
    # source_row_number preserves repeated keys instead of silently replacing them.
    for table_name, linked_column in LINKED_DATASETS.values():
        cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            snapshot_id TEXT NOT NULL,
            report_date TEXT NOT NULL,
            source_row_number INTEGER NOT NULL,
            country TEXT NOT NULL,
            {linked_column} TEXT NOT NULL,
            worker_count INTEGER NOT NULL,
            PRIMARY KEY (snapshot_id, source_row_number),
            FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id)
        )
        """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS snapshot_dataset_quality (
        snapshot_id TEXT NOT NULL,
        dataset_name TEXT NOT NULL,
        row_count INTEGER NOT NULL,
        distinct_key_count INTEGER NOT NULL,
        worker_total INTEGER NOT NULL,
        expected_worker_total INTEGER,
        status TEXT NOT NULL,
        details TEXT NOT NULL,
        PRIMARY KEY (snapshot_id, dataset_name),
        FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id)
    )
    """)
    # Legacy table retained for compatibility. New data uses the lossless _rows table.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS monthly_country_province (
        snapshot_id TEXT NOT NULL,
        report_date TEXT NOT NULL,
        country TEXT NOT NULL,
        province TEXT NOT NULL,
        worker_count INTEGER NOT NULL,
        PRIMARY KEY (snapshot_id, country, province),
        FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id)
    )
    """)
    conn.commit()
    conn.close()


def is_snapshot_ingested(report_date, db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    conn = get_connection(db_path)
    row = conn.execute(
        """
        SELECT snapshot_id FROM snapshots
        WHERE report_date = ?
          AND total_workers > 0
          AND total_countries > 0
          AND total_provinces > 0
          AND (
              SELECT COUNT(*)
              FROM snapshot_dataset_quality q
              WHERE q.snapshot_id = snapshots.snapshot_id
                AND q.dataset_name IN (
                    '01_destination_countries', '02_provinces', '03_districts',
                    '04_job_titles', '05_travel_methods', '06_education_levels',
                    '07_embassy_labour_offices', '08_gender'
                )
                AND q.status IN ('complete', 'partial')
          ) = 8
        """,
        (report_date,),
    ).fetchone()
    conn.close()
    return row is not None


def _write_csv_set(directory, tables_dict):
    os.makedirs(directory, exist_ok=True)
    expected_files = {f"{name}.csv" for name in tables_dict}
    for filename in os.listdir(directory):
        if filename.endswith(".csv") and filename not in expected_files:
            os.remove(os.path.join(directory, filename))
    for name, (header, rows) in tables_dict.items():
        csv_file = os.path.join(directory, f"{name}.csv")
        with open(csv_file, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)


def _dataset_quality(dataset_name, rows, expected_total, linked):
    key_width = 2 if linked else 1
    keys = []
    counts = []
    invalid_rows = 0
    for row in rows:
        if len(row) <= key_width:
            invalid_rows += 1
            continue
        try:
            count = _parse_count(row[key_width])
        except (TypeError, ValueError):
            invalid_rows += 1
            continue
        if count < 0:
            invalid_rows += 1
            continue
        keys.append(_normalized_key(row[:key_width]))
        counts.append(count)
    distinct_keys = len(set(keys))
    duplicate_rows = len(keys) - distinct_keys
    worker_total = sum(counts)
    details = []
    if invalid_rows:
        details.append(f"{invalid_rows} invalid rows")
    if duplicate_rows:
        details.append(f"{duplicate_rows} duplicate key rows preserved")
    if expected_total is not None and worker_total != expected_total:
        details.append(f"worker total differs from snapshot by {worker_total - expected_total:+d}")
    if invalid_rows:
        status = "invalid"
    elif linked and (duplicate_rows or worker_total != expected_total):
        status = "invalid"
    elif worker_total != expected_total:
        status = "partial"
    else:
        status = "complete"
    return (
        dataset_name, len(rows), distinct_keys, worker_total, expected_total,
        status, "; ".join(details) if details else "reconciles to snapshot total",
    )


def save_snapshot(
    report_date,
    tables_dict,
    db_path=DEFAULT_DB_PATH,
    snapshots_dir=DEFAULT_SNAPSHOTS_DIR,
    current_dir=DEFAULT_CURRENT_DIR,
):
    validate_snapshot_tables(tables_dict)
    init_db(db_path)
    scraped_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    safe_date_tag = report_date.replace("/", "-").replace(".", "-").replace(" ", "_")
    snapshot_id = f"snapshot_{safe_date_tag}"
    folder = os.path.join(snapshots_dir, safe_date_tag)
    storage_root = os.path.dirname(os.path.abspath(db_path))
    stored_folder = os.path.relpath(os.path.abspath(folder), storage_root)
    country_rows = tables_dict["01_destination_countries"][1]
    total_countries = len(country_rows)
    total_workers = sum(_parse_count(row[1]) for row in country_rows)
    total_provinces = len(tables_dict["02_provinces"][1])

    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("""
        INSERT OR REPLACE INTO snapshots
            (snapshot_id, report_date, scraped_at, total_workers, total_countries,
             total_provinces, snapshot_dir)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            snapshot_id, report_date, scraped_at, total_workers, total_countries,
            total_provinces, stored_folder,
        ))
        storage_tables = [spec[0] for spec in BASE_DATASETS.values()]
        storage_tables += [spec[0] for spec in LINKED_DATASETS.values()]
        storage_tables.append("snapshot_dataset_quality")
        for table_name in storage_tables:
            cur.execute(f"DELETE FROM {table_name} WHERE snapshot_id = ?", (snapshot_id,))

        for dataset_name, (table_name, key_column, _) in BASE_DATASETS.items():
            rows = tables_dict[dataset_name][1]
            for row in rows:
                cur.execute(
                    f"""INSERT INTO {table_name}
                    (snapshot_id, report_date, {key_column}, worker_count)
                    VALUES (?, ?, ?, ?)""",
                    (snapshot_id, report_date, str(row[0]).strip(), _parse_count(row[1])),
                )
            quality = _dataset_quality(dataset_name, rows, total_workers, linked=False)
            cur.execute("""
                INSERT INTO snapshot_dataset_quality
                (snapshot_id, dataset_name, row_count, distinct_key_count,
                 worker_total, expected_worker_total, status, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (snapshot_id,) + quality)

        for dataset_name, (table_name, linked_column) in LINKED_DATASETS.items():
            rows = tables_dict.get(dataset_name, ([], []))[1]
            for source_row_number, row in enumerate(rows, 1):
                if len(row) < 3:
                    continue
                cur.execute(
                    f"""INSERT INTO {table_name}
                    (snapshot_id, report_date, source_row_number, country,
                     {linked_column}, worker_count)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        snapshot_id, report_date, source_row_number,
                        str(row[0]).strip(), str(row[1]).strip(), _parse_count(row[2]),
                    ),
                )
            quality = _dataset_quality(dataset_name, rows, total_workers, linked=True)
            cur.execute("""
                INSERT INTO snapshot_dataset_quality
                (snapshot_id, dataset_name, row_count, distinct_key_count,
                 worker_total, expected_worker_total, status, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (snapshot_id,) + quality)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Publish files only after the database transaction succeeds.
    _write_csv_set(folder, tables_dict)
    _write_csv_set(current_dir, tables_dict)
    return snapshot_id, folder
