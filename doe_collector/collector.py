# -*- coding: utf-8 -*-
"""
Playwright-based collector for DOE Overseas Labour Looker Studio dashboard.
"""

import asyncio
import json
import csv
import re
import os
from datetime import datetime
from playwright.async_api import async_playwright
from .storage import is_snapshot_ingested, save_snapshot

LOOKER_STUDIO_URL = "https://datastudio.google.com/u/0/reporting/89607282-a4c6-4a0c-b6e9-a2934c16d885/page/p_rawnmuf5lc"


class CollectionError(RuntimeError):
    """Raised when the dashboard did not produce a trustworthy snapshot."""


def classify_dimension_table(values):
    """Return the canonical dataset name and header for a Looker table."""
    values = [str(value).strip() for value in values if str(value).strip()]
    value_set = set(values)
    if not values:
        return None

    if {"ชาย", "หญิง"}.issubset(value_set):
        return "08_gender", ["gender", "worker_count"]

    education_markers = ("มัธยม", "ประถม", "ปริญญา", "ประกาศนียบัตร", "วุฒิการศึกษา")
    if sum(any(marker in value for marker in education_markers) for value in values) >= 2:
        return "06_education_levels", ["education_level", "worker_count"]

    travel_values = {
        "บริษัทจัดส่ง", "กรมจัดส่ง", "RE-ENTRY", "VISA RE-ENTRY",
        "เดินทางด้วยตนเอง", "นายจ้างพาไปทำงาน", "นายจ้างส่งไปฝึกงาน",
    }
    if len(value_set.intersection(travel_values)) >= 2:
        return "05_travel_methods", ["travel_method", "worker_count"]

    embassy_markers = ("สนร.", "สถานเอกอัครราชทูต", "สำนักงานแรงงาน")
    if sum(any(marker in value for marker in embassy_markers) for value in values) >= 2:
        return "07_embassy_labour_offices", ["embassy_labour_office", "worker_count"]

    if len(values) >= 500 and any(
        value.startswith("อำเภอ") or value.startswith("เขต") for value in values
    ):
        return "03_districts", ["district", "worker_count"]

    known_provinces = {"อุดรธานี", "นครราชสีมา", "เชียงราย", "ขอนแก่น"}
    if 70 <= len(values) <= 90 and value_set.intersection(known_provinces):
        return "02_provinces", ["province", "worker_count"]

    if len(values) > 50 and value_set.intersection({"ไต้หวัน", "อิสราเอล"}):
        return "01_destination_countries", ["destination_country", "worker_count"]

    known_jobs = {"คนงานเกษตร", "ผลิตผลิตภัณฑ์โลหะ"}
    if len(values) >= 500 and value_set.intersection(known_jobs):
        return "04_job_titles", ["standard_job_title", "worker_count"]

    return None


def extract_report_date(body_text):
    thai_date_matches = re.findall(
        r'(\d{1,2}\s*\.?\s*[ก-๙]+(?:\.[ก-๙]+)*\.?\s*\d{4})',
        body_text,
    )
    if thai_date_matches:
        return thai_date_matches[0]

    updated_match = re.search(
        r'Data Last Updated:\s*(\d{1,2})/(\d{1,2})/(\d{4})',
        body_text,
        re.IGNORECASE,
    )
    if updated_match:
        month, _, year = updated_match.groups()
        return f"{year}-{int(month):02d}"

    raise CollectionError(
        "Could not find the dashboard report date; refusing to create a fallback snapshot"
    )


async def load_dashboard_report(page, wait_seconds, attempts=3):
    """Load Looker with bounded retries and return its verified report date."""
    body_text = ""
    for attempt in range(1, attempts + 1):
        if attempt == 1:
            await page.goto(LOOKER_STUDIO_URL, wait_until="domcontentloaded")
        else:
            print(f"Dashboard date not rendered; retrying load ({attempt}/{attempts})...")
            await page.reload(wait_until="domcontentloaded")
        await asyncio.sleep(wait_seconds)
        body_text = await page.evaluate("() => document.body.innerText")
        try:
            return extract_report_date(body_text), body_text
        except CollectionError:
            pass

    preview = " ".join(body_text.split())[:240] or "<empty page>"
    raise CollectionError(
        "Could not find the dashboard report date after "
        f"{attempts} attempts; page preview: {preview}"
    )


async def check_dashboard_update():
    """Quickly check the report's current update date without crawling all tables."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        
        report_date, body_text = await load_dashboard_report(page, wait_seconds=15)
        
        await browser.close()
        return report_date, body_text

async def collect_monthly_data(force=False, include_linked=False):
    """
    Main extraction pipeline:
    1. Checks report update date.
    2. If new (or forced), extracts all baseline dimension tables.
    3. If include_linked=True, also crawls country-level cross-tabulations.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        
        captured_responses = []

        async def on_response(resp):
            if "batchedData" in resp.url:
                try:
                    text = await resp.text()
                    if text.startswith(")]}'"):
                        text = text[4:].strip()
                    captured_responses.append(json.loads(text))
                except Exception:
                    pass

        page.on("response", on_response)
        
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Connecting to DOE Looker Studio...")
        # The dashboard can emit bootstrap responses before its data tables arrive.
        # Retry boundedly because Looker occasionally renders an empty shell.
        report_date, body_text = await load_dashboard_report(page, wait_seconds=20)
        
        print(f"Dashboard Report Date: {report_date}")
        
        if not force and is_snapshot_ingested(report_date):
            print(f"Snapshot for '{report_date}' has already been ingested. Use --force to re-crawl.")
            await browser.close()
            return report_date, False
            
        print("Extracting baseline dimension tables...")
        raw_tables = {}
        
        for resp in captured_responses:
            for dr in resp.get("dataResponse", []):
                for ds in dr.get("dataSubset", []):
                    tdata = ds.get("dataset", {}).get("tableDataset", {})
                    cols = tdata.get("column", [])
                    if len(cols) >= 2:
                        vals = cols[0].get("stringColumn", {}).get("values", [])
                        counts = cols[1].get("longColumn", {}).get("values", [])
                        if not vals or not counts:
                            continue
                        
                        classification = classify_dimension_table(vals)
                        if classification:
                            dataset_name, header = classification
                            raw_tables[dataset_name] = (header, list(zip(vals, counts)))

        print(f"Extracted {len(raw_tables)} base dimension tables.")
        
        # If linked crawl requested
        if include_linked and "01_destination_countries" in raw_tables:
            countries = [r[0] for r in raw_tables["01_destination_countries"][1]]
            print(f"Crawling cross-tabulation for {len(countries)} countries...")
            linked_prov = []
            linked_dist = []
            linked_job = []
            
            for c_idx, c_name in enumerate(countries[:25]): # Top 25 countries
                print(f"  [{c_idx+1}/25] Filtering for {c_name}...")
                await page.mouse.click(600, 330)
                await asyncio.sleep(1)
                captured_responses.clear()
                
                clicked = await page.evaluate(f'''async () => {{
                    const items = Array.from(document.querySelectorAll('.item.item-single'));
                    for (const item of items) {{
                        if (item.innerText && item.innerText.includes('{c_name}')) {{
                            item.click();
                            return true;
                        }}
                    }}
                    return false;
                }}''')
                
                await page.mouse.click(500, 100)
                await asyncio.sleep(3.5)
                
                for resp in captured_responses:
                    for dr in resp.get("dataResponse", []):
                        for ds in dr.get("dataSubset", []):
                            tdata = ds.get("dataset", {}).get("tableDataset", {})
                            cols = tdata.get("column", [])
                            if len(cols) >= 2:
                                vals = cols[0].get("stringColumn", {}).get("values", [])
                                counts = cols[1].get("longColumn", {}).get("values", [])
                                if vals and counts:
                                    sample = vals[0]
                                    if any(p in sample for p in ['อุดรธานี', 'นครราชสีมา', 'เชียงราย']):
                                        for v, cnt in zip(vals, counts):
                                            linked_prov.append([c_name, v, cnt])
                                    elif 'อำเภอ' in str(sample) or 'เขต' in str(sample):
                                        for v, cnt in zip(vals, counts):
                                            linked_dist.append([c_name, v, cnt])
                                    elif len(vals) > 0 and sample not in countries:
                                        for v, cnt in zip(vals, counts):
                                            linked_job.append([c_name, v, cnt])
                                            
            raw_tables["linked_country_by_province"] = (["destination_country", "province", "worker_count"], linked_prov)
            raw_tables["linked_country_by_district"] = (["destination_country", "district", "worker_count"], linked_dist)
            raw_tables["linked_country_by_job"] = (["destination_country", "standard_job_title", "worker_count"], linked_job)

        snapshot_id, folder = save_snapshot(report_date, raw_tables)
        print(f"Successfully saved snapshot {snapshot_id} to {folder}")
        
        await browser.close()
        return report_date, True
