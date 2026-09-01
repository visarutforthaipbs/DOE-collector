# 🌍 DOE Collector — Thailand Overseas Labour Monitor

Automated monthly monitoring, data ingestion, and time-series change detection for **Thailand's Department of Employment (DOE)** Overseas Labour Data (`กองบริหารแรงงานไทยไปต่างประเทศ กรมการจัดหางาน กระทรวงแรงงาน`).

---

## 📦 What is in this Project?

* **Live Data Collector:** Headless Playwright engine that queries the Department of Employment's Looker Studio dashboard.
* **1D & 2D Datasets:** 
  * 140 Destination Countries
  * 77 Provinces & 923 Districts of origin
  * 1,000+ Standardized occupations
  * Deployment channels (Agencies, G2G, Re-Entry)
  * Gender & Education levels
  * Linked cross-tabulations (Country $\times$ Province, Country $\times$ Job, etc.)
* **Historical SQLite Database (`doe_labour_monitoring.db`):** Time-series storage tracking monthly trends and net population shifts.
* **Automated Diff Engine:** Month-over-month ($\Delta$) growth and decline reports.

---

## ⚡ Quick Start

```bash
cd /Users/lighthouse-control/Desktop/DOE-collector

# 1. Ingest baseline snapshot into database
.venv/bin/python -m doe_collector ingest-local --date "1.ก.ย.2569"

# 2. Check if a new update is published on the portal
.venv/bin/python -m doe_collector check

# 3. Fetch latest data
.venv/bin/python -m doe_collector fetch

# 4. View Month-over-Month Delta
.venv/bin/python -m doe_collector diff

# 5. List all snapshots in the database
.venv/bin/python -m doe_collector list
```

---

## 📁 Project Structure

```
DOE-collector/
├── .venv/                      # Python 3.9 virtual environment
├── doe_collector/              # Core python package
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py                  # CLI command handlers
│   ├── collector.py            # Playwright scraping engine
│   ├── storage.py              # SQLite & snapshot manager
│   └── diff_engine.py          # MoM change analysis
├── data/
│   ├── current/                # Current master CSV datasets
│   └── snapshots/              # Archived monthly snapshots (1-ก-ย-2569/, etc.)
├── doe_labour_monitoring.db    # SQLite Master Time-Series DB
├── requirements.txt
├── AGENTS.md                   # AI Agent guidance
└── README.md
```
