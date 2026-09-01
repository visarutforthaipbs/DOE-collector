# -*- coding: utf-8 -*-
"""
CLI Interface for DOE Overseas Labour Collector & Monitor.
"""

import sys
import os
import argparse
import asyncio
from .collector import collect_monthly_data, check_dashboard_update
from .storage import init_db, is_snapshot_ingested, save_snapshot, get_connection, DEFAULT_CURRENT_DIR
from .diff_engine import compute_monthly_diff

def main():
    parser = argparse.ArgumentParser(
        prog="python -m doe_collector",
        description="DOE Collector — Thailand Department of Employment Overseas Labour Monitor"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # 1. check
    subparsers.add_parser("check", help="Check the latest update date on the DOE Looker Studio dashboard")

    # 2. fetch
    fetch_p = subparsers.add_parser("fetch", help="Fetch and ingest the latest monthly snapshot")
    fetch_p.add_argument("--force", action="store_true", help="Force re-fetch even if already ingested")
    fetch_p.add_argument("--include-linked", action="store_true", help="Also crawl linked country cross-tabulations")

    # 3. diff
    subparsers.add_parser("diff", help="Generate month-over-month change report")

    # 4. list
    subparsers.add_parser("list", help="List all ingested snapshots and record counts")

    # 5. ingest-local
    ingest_p = subparsers.add_parser("ingest-local", help="Ingest local CSV files from data/current/ into database")
    ingest_p.add_argument("--date", default="1.ก.ย.2569", help="Report date label")

    # 6. audit
    subparsers.add_parser("audit", help="Show completeness and reconciliation for every dataset")

    args = parser.parse_args()

    if args.command == "check":
        print("Checking DOE Looker Studio dashboard update date...")
        report_date, _ = asyncio.run(check_dashboard_update())
        ingested = is_snapshot_ingested(report_date)
        status = "ALREADY INGESTED" if ingested else "NEW DATA AVAILABLE"
        print(f"Latest Report Date: {report_date} [{status}]")

    elif args.command == "fetch":
        report_date, updated = asyncio.run(collect_monthly_data(force=args.force, include_linked=args.include_linked))
        if updated:
            print("\n" + compute_monthly_diff())

    elif args.command == "diff":
        print(compute_monthly_diff())

    elif args.command == "list":
        init_db()
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT snapshot_id, report_date, scraped_at, total_workers, total_countries, total_provinces FROM snapshots ORDER BY scraped_at DESC")
        rows = cur.fetchall()
        conn.close()
        
        print(f"{'Snapshot ID':<25} | {'Report Date':<15} | {'Workers':<10} | {'Countries':<10} | {'Scraped At'}")
        print("-" * 80)
        for r in rows:
            print(f"{r['snapshot_id']:<25} | {r['report_date']:<15} | {r['total_workers']:<10,}"
                  f" | {r['total_countries']:<10} | {r['scraped_at']}")

    elif args.command == "ingest-local":
        import csv
        local_dir = DEFAULT_CURRENT_DIR
        print(f"Ingesting local CSV files from {local_dir}...")
        tables = {}
        if os.path.exists(local_dir):
            for fname in os.listdir(local_dir):
                if fname.endswith(".csv"):
                    tname = fname[:-4]
                    fpath = os.path.join(local_dir, fname)
                    with open(fpath, encoding="utf-8-sig") as fp:
                        reader = list(csv.reader(fp))
                        if reader:
                            tables[tname] = (reader[0], reader[1:])
        sid, fld = save_snapshot(args.date, tables)
        print(f"Ingested baseline data into snapshot '{sid}' ({len(tables)} tables).")

    elif args.command == "audit":
        init_db()
        conn = get_connection()
        rows = conn.execute("""
            SELECT q.dataset_name, q.row_count, q.distinct_key_count,
                   q.worker_total, q.expected_worker_total, q.status, q.details
            FROM snapshot_dataset_quality q
            JOIN snapshots s ON s.snapshot_id = q.snapshot_id
            WHERE q.snapshot_id = (
                SELECT snapshot_id FROM snapshots ORDER BY scraped_at DESC LIMIT 1
            )
            ORDER BY q.dataset_name
        """).fetchall()
        conn.close()
        if not rows:
            print("No dataset audit is available. Re-fetch or run ingest-local first.")
        else:
            print(f"{'Dataset':<38} | {'Rows':>6} | {'Unique':>6} | {'Workers':>10} | {'Status':<8}")
            print("-" * 85)
            for row in rows:
                print(
                    f"{row['dataset_name']:<38} | {row['row_count']:>6,} | "
                    f"{row['distinct_key_count']:>6,} | {row['worker_total']:>10,} | "
                    f"{row['status']:<8}"
                )
                if row['details'] != "reconciles to snapshot total":
                    print(f"  {row['details']}")

    else:
        parser.print_help()

if __name__ == "__main__":
    main()
