# DOE Collector — Agent Guide

**Package**: `/Users/lighthouse-control/Desktop/DOE-collector/doe_collector/`  
**Python**: `.venv/bin/python` (3.9) — NEVER use system `python3` (3.14, no deps)

## Quick Start — Always do this first

```bash
cd /Users/lighthouse-control/Desktop/DOE-collector

# 1. Ingest baseline data into SQLite database
.venv/bin/python -m doe_collector ingest-local --date "1.ก.ย.2569"

# 2. Check if a new monthly snapshot is published on DOE Looker Studio
.venv/bin/python -m doe_collector check

# 3. Fetch and ingest the latest monthly snapshot
.venv/bin/python -m doe_collector fetch

# 4. View Month-over-Month changes (MoM Diff Report)
.venv/bin/python -m doe_collector diff

# 5. List all snapshots in the database
.venv/bin/python -m doe_collector list
```

## What this package does

Automated pipeline and time-series monitoring for Thailand's Department of Employment (DOE) Overseas Labour statistics (`กองบริหารแรงงานไทยไปต่างประเทศ กรมการจัดหางาน กระทรวงแรงงาน`):

| Component | Description |
| :--- | :--- |
| `doe_collector.collector` | Headless Playwright scraper that queries Looker Studio backend RPCs |
| `doe_collector.storage` | SQLite time-series database (`doe_labour_monitoring.db`) + Snapshot archive |
| `doe_collector.diff_engine` | Month-over-month delta ($\Delta$) calculator & alert engine |
| `doe_collector.cli` | CLI commands (`check`, `fetch`, `diff`, `list`, `ingest-local`) |

## Data Directory Structure

```
DOE-collector/
├── data/
│   ├── current/                  ← Latest active CSV tables
│   │   ├── 01_destination_countries.csv
│   │   ├── 02_provinces.csv
│   │   ├── 03_districts.csv
│   │   ├── 04_job_titles.csv
│   │   ├── 05_travel_methods.csv
│   │   ├── 06_education_levels.csv
│   │   ├── 07_embassy_labour_offices.csv
│   │   ├── 08_gender.csv
│   │   ├── linked_country_by_province.csv
│   │   ├── linked_country_by_district.csv
│   │   └── linked_country_by_job.csv
│   └── snapshots/
│       └── 1-ก-ย-2569/           ← Monthly raw archive snapshots
├── doe_labour_monitoring.db      ← SQLite time-series master database
```

## Scheduling

### Local Cron (macOS / Linux)
```bash
crontab -e
```
Add:
```cron
0 9 2 * * cd /Users/lighthouse-control/Desktop/DOE-collector && .venv/bin/python -m doe_collector fetch >> /tmp/doe_collector.log 2>&1
```
