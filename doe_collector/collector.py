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


def extract_report_date(body_text):
    date_matches = re.findall(
        r'(\d{1,2}\s*\.?\s*[ก-๙]+(?:\.[ก-๙]+)*\.?\s*\d{4})',
        body_text,
    )
    if not date_matches:
        raise CollectionError(
            "Could not find the dashboard report date; refusing to create a fallback snapshot"
        )
    return date_matches[0]

async def check_dashboard_update():
    """Quickly check the report's current update date without crawling all tables."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        
        await page.goto(LOOKER_STUDIO_URL, wait_until="domcontentloaded")
        await asyncio.sleep(8)
        
        body_text = await page.evaluate("() => document.body.innerText")
        report_date = extract_report_date(body_text)
        
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
        await page.goto(LOOKER_STUDIO_URL, wait_until="domcontentloaded")
        # Network responses can arrive later on headless/cloud runners.
        for _ in range(30):
            if captured_responses:
                break
            await asyncio.sleep(1)
        
        body_text = await page.evaluate("() => document.body.innerText")
        report_date = extract_report_date(body_text)
        
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
                        
                        sample = vals[0]
                        if sample in ['ชาย', 'หญิง']:
                            raw_tables["08_gender"] = (["gender", "worker_count"], list(zip(vals, counts)))
                        elif any(x in sample for x in ['มัธยม', 'ประถม', 'ปริญญา', 'ประกาศนียบัตร']):
                            raw_tables["06_education_levels"] = (["education_level", "worker_count"], list(zip(vals, counts)))
                        elif sample in ['บริษัทจัดส่ง', 'กรมจัดส่ง', 'RE-ENTRY', 'VISA RE-ENTRY', 'เดินทางด้วยตนเอง']:
                            raw_tables["05_travel_methods"] = (["travel_method", "worker_count"], list(zip(vals, counts)))
                        elif any(p in sample for p in ['อุดรธานี', 'นครราชสีมา', 'เชียงราย', 'ขอนแก่น']):
                            if len(vals) == 77:
                                raw_tables["02_provinces"] = (["province", "worker_count"], list(zip(vals, counts)))
                        elif 'อำเภอ' in str(sample) or 'เขต' in str(sample):
                            if len(vals) > 500:
                                raw_tables["03_districts"] = (["district", "worker_count"], list(zip(vals, counts)))
                        elif 'ไต้หวัน' in vals or 'อิสราเอล' in vals:
                            if len(vals) > 50:
                                raw_tables["01_destination_countries"] = (["destination_country", "worker_count"], list(zip(vals, counts)))
                        elif 'คนงานเกษตร' in vals or 'ผลิตผลิตภัณฑ์โลหะ' in vals:
                            if len(vals) > 500:
                                raw_tables["04_job_titles"] = (["standard_job_title", "worker_count"], list(zip(vals, counts)))

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
