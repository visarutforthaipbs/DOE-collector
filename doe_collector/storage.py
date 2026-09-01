# -*- coding: utf-8 -*-
"""
Database and snapshot storage engine for DOE Collector.
"""

import sqlite3
import os
import csv
from datetime import datetime

DEFAULT_PROJECT_DIR = os.path.dirname(os.path.dirname(__file__))
DEFAULT_DB_PATH = os.path.join(DEFAULT_PROJECT_DIR, "doe_labour_monitoring.db")
DEFAULT_SNAPSHOTS_DIR = os.path.join(DEFAULT_PROJECT_DIR, "data", "snapshots")
DEFAULT_CURRENT_DIR = os.path.join(DEFAULT_PROJECT_DIR, "data", "current")


class SnapshotValidationError(ValueError):
    """Raised when a scrape is incomplete and must not be persisted."""


def validate_snapshot_tables(tables_dict):
    """Reject empty or obviously partial dashboard extractions."""
    required = {
        "01_destination_countries": 50,
        "02_provinces": 70,
    }
    problems = []

    for table_name, minimum_rows in required.items():
        table = tables_dict.get(table_name)
        rows = table[1] if table else []
        if len(rows) < minimum_rows:
            problems.append(
                f"{table_name} has {len(rows)} rows; expected at least {minimum_rows}"
            )
            continue

        valid_counts = []
        for row in rows:
            if len(row) < 2:
                continue
            try:
                count = int(str(row[1]).replace(",", ""))
            except (TypeError, ValueError):
                continue
            if count >= 0:
                valid_counts.append(count)

        if len(valid_counts) != len(rows):
            problems.append(f"{table_name} contains invalid worker counts")
        elif table_name == "01_destination_countries" and sum(valid_counts) <= 0:
            problems.append("destination-country worker total is zero")

    if problems:
        raise SnapshotValidationError(
            "Snapshot validation failed: " + "; ".join(problems)
        )

def get_connection(db_path=DEFAULT_DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(db_path=DEFAULT_DB_PATH):
    conn = get_connection(db_path)
    cur = conn.cursor()
    
    # 1. Snapshots metadata table
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
    
    # 2. Country monthly stats
    cur.execute("""
    CREATE TABLE IF NOT EXISTS monthly_country_stats (
        snapshot_id TEXT NOT NULL,
        report_date TEXT NOT NULL,
        country TEXT NOT NULL,
        worker_count INTEGER NOT NULL,
        PRIMARY KEY (snapshot_id, country),
        FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id)
    )
    """)
    
    # 3. Province monthly stats
    cur.execute("""
    CREATE TABLE IF NOT EXISTS monthly_province_stats (
        snapshot_id TEXT NOT NULL,
        report_date TEXT NOT NULL,
        province TEXT NOT NULL,
        worker_count INTEGER NOT NULL,
        PRIMARY KEY (snapshot_id, province),
        FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id)
    )
    """)
    
    # 4. Job monthly stats
    cur.execute("""
    CREATE TABLE IF NOT EXISTS monthly_job_stats (
        snapshot_id TEXT NOT NULL,
        report_date TEXT NOT NULL,
        job_title TEXT NOT NULL,
        worker_count INTEGER NOT NULL,
        PRIMARY KEY (snapshot_id, job_title),
        FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id)
    )
    """)
    
    # 5. Travel method monthly stats
    cur.execute("""
    CREATE TABLE IF NOT EXISTS monthly_travel_stats (
        snapshot_id TEXT NOT NULL,
        report_date TEXT NOT NULL,
        travel_method TEXT NOT NULL,
        worker_count INTEGER NOT NULL,
        PRIMARY KEY (snapshot_id, travel_method),
        FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id)
    )
    """)
    
    # 6. Linked Country x Province
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
    cur = conn.cursor()
    cur.execute(
        """
        SELECT snapshot_id FROM snapshots
        WHERE report_date = ?
          AND total_workers > 0
          AND total_countries > 0
          AND total_provinces > 0
        """,
        (report_date,),
    )
    row = cur.fetchone()
    conn.close()
    return row is not None

def save_snapshot(report_date, tables_dict, db_path=DEFAULT_DB_PATH, snapshots_dir=DEFAULT_SNAPSHOTS_DIR, current_dir=DEFAULT_CURRENT_DIR):
    validate_snapshot_tables(tables_dict)
    init_db(db_path)
    
    scraped_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    safe_date_tag = report_date.replace("/", "-").replace(".", "-").replace(" ", "_")
    snapshot_id = f"snapshot_{safe_date_tag}"
    
    # Create snapshot folder and update current
    folder = os.path.join(snapshots_dir, safe_date_tag)
    storage_root = os.path.dirname(os.path.abspath(db_path))
    stored_folder = os.path.relpath(os.path.abspath(folder), storage_root)
    os.makedirs(folder, exist_ok=True)
    os.makedirs(current_dir, exist_ok=True)
    
    # Write CSVs to snapshot and current
    for name, (header, rows) in tables_dict.items():
        # Snapshot copy
        csv_file = os.path.join(folder, f"{name}.csv")
        with open(csv_file, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
            
        # Current copy
        curr_file = os.path.join(current_dir, f"{name}.csv")
        with open(curr_file, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
            
    # Compute totals
    total_workers = 0
    total_countries = 0
    if "01_destination_countries" in tables_dict:
        rows = tables_dict["01_destination_countries"][1]
        total_countries = len(rows)
        total_workers = sum(int(r[1]) for r in rows if len(r) > 1 and str(r[1]).isdigit())
        
    total_provinces = len(tables_dict.get("02_provinces", ([], []))[1])
    
    # Save to SQLite
    conn = get_connection(db_path)
    cur = conn.cursor()
    
    cur.execute("""
    INSERT OR REPLACE INTO snapshots (snapshot_id, report_date, scraped_at, total_workers, total_countries, total_provinces, snapshot_dir)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (snapshot_id, report_date, scraped_at, total_workers, total_countries, total_provinces, stored_folder))

    # Replacing a forced snapshot must not leave rows that disappeared upstream.
    for table_name in (
        "monthly_country_stats",
        "monthly_province_stats",
        "monthly_job_stats",
        "monthly_travel_stats",
        "monthly_country_province",
    ):
        cur.execute(f"DELETE FROM {table_name} WHERE snapshot_id = ?", (snapshot_id,))
    
    if "01_destination_countries" in tables_dict:
        for r in tables_dict["01_destination_countries"][1]:
            if len(r) >= 2 and str(r[1]).isdigit():
                cur.execute("""
                INSERT OR REPLACE INTO monthly_country_stats (snapshot_id, report_date, country, worker_count)
                VALUES (?, ?, ?, ?)
                """, (snapshot_id, report_date, r[0], int(r[1])))
                
    if "02_provinces" in tables_dict:
        for r in tables_dict["02_provinces"][1]:
            if len(r) >= 2 and str(r[1]).isdigit():
                cur.execute("""
                INSERT OR REPLACE INTO monthly_province_stats (snapshot_id, report_date, province, worker_count)
                VALUES (?, ?, ?, ?)
                """, (snapshot_id, report_date, r[0], int(r[1])))

    if "04_job_titles" in tables_dict:
        for r in tables_dict["04_job_titles"][1]:
            if len(r) >= 2 and str(r[1]).isdigit():
                cur.execute("""
                INSERT OR REPLACE INTO monthly_job_stats (snapshot_id, report_date, job_title, worker_count)
                VALUES (?, ?, ?, ?)
                """, (snapshot_id, report_date, r[0], int(r[1])))

    if "05_travel_methods" in tables_dict:
        for r in tables_dict["05_travel_methods"][1]:
            if len(r) >= 2 and str(r[1]).isdigit():
                cur.execute("""
                INSERT OR REPLACE INTO monthly_travel_stats (snapshot_id, report_date, travel_method, worker_count)
                VALUES (?, ?, ?, ?)
                """, (snapshot_id, report_date, r[0], int(r[1])))
                
    if "linked_country_by_province" in tables_dict:
        for r in tables_dict["linked_country_by_province"][1]:
            if len(r) >= 3 and str(r[2]).isdigit():
                cur.execute("""
                INSERT OR REPLACE INTO monthly_country_province (snapshot_id, report_date, country, province, worker_count)
                VALUES (?, ?, ?, ?, ?)
                """, (snapshot_id, report_date, r[0], r[1], int(r[2])))
                
    conn.commit()
    conn.close()
    
    return snapshot_id, folder
