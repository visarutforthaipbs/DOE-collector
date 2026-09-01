# -*- coding: utf-8 -*-
"""
Month-over-Month comparison and change detection engine for DOE Collector.
"""

from .storage import get_connection

def compute_monthly_diff():
    """
    Computes month-over-month changes across countries, provinces, jobs, and deployment channels.
    """
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT snapshot_id, report_date, scraped_at, total_workers,
               total_countries, total_provinces
        FROM snapshots
        WHERE total_workers > 0
          AND total_countries > 0
          AND total_provinces > 0
        ORDER BY scraped_at ASC
    """)
    snapshots = cur.fetchall()
    
    if len(snapshots) < 1:
        conn.close()
        return "No snapshots available in the database."
        
    latest = snapshots[-1]
    
    if len(snapshots) == 1:
        conn.close()
        return f"""# 📊 DOE Labour Monitoring Snapshot: {latest['report_date']}
* **Status:** Initial baseline snapshot recorded.
* **Total Active Workers:** {latest['total_workers']:,}
* **Destination Countries:** {latest['total_countries']}
* **Provinces:** {latest['total_provinces']}

*(Month-over-month deltas will automatically appear once the second monthly snapshot is ingested).*
"""

    prev = snapshots[-2]
    
    worker_diff = latest['total_workers'] - prev['total_workers']
    worker_pct = (worker_diff / prev['total_workers']) * 100 if prev['total_workers'] > 0 else 0
    
    # Country deltas
    cur.execute("""
    SELECT 
        COALESCE(curr.country, prev_c.country) AS country,
        COALESCE(prev_c.worker_count, 0) AS prev_count,
        COALESCE(curr.worker_count, 0) AS curr_count,
        (COALESCE(curr.worker_count, 0) - COALESCE(prev_c.worker_count, 0)) AS diff
    FROM (SELECT * FROM monthly_country_stats WHERE snapshot_id = ?) curr
    FULL OUTER JOIN (SELECT * FROM monthly_country_stats WHERE snapshot_id = ?) prev_c
        ON curr.country = prev_c.country
    ORDER BY diff DESC
    """, (latest['snapshot_id'], prev['snapshot_id']))
    country_diffs = cur.fetchall()
    
    top_gainers = [c for c in country_diffs if c['diff'] > 0][:5]
    top_losers = sorted([c for c in country_diffs if c['diff'] < 0], key=lambda x: x['diff'])[:5]
    
    # Province deltas
    cur.execute("""
    SELECT 
        COALESCE(curr.province, prev_p.province) AS province,
        COALESCE(prev_p.worker_count, 0) AS prev_count,
        COALESCE(curr.worker_count, 0) AS curr_count,
        (COALESCE(curr.worker_count, 0) - COALESCE(prev_p.worker_count, 0)) AS diff
    FROM (SELECT * FROM monthly_province_stats WHERE snapshot_id = ?) curr
    FULL OUTER JOIN (SELECT * FROM monthly_province_stats WHERE snapshot_id = ?) prev_p
        ON curr.province = prev_p.province
    ORDER BY diff DESC
    """, (latest['snapshot_id'], prev['snapshot_id']))
    province_diffs = cur.fetchall()
    
    top_prov_gainers = [p for p in province_diffs if p['diff'] > 0][:5]
    top_prov_losers = sorted([p for p in province_diffs if p['diff'] < 0], key=lambda x: x['diff'])[:5]
    
    conn.close()
    
    sign = "+" if worker_diff >= 0 else ""
    report_md = f"""# 📈 Monthly Labour Monitor Diff Report
**Period:** {prev['report_date']} $\\rightarrow$ **{latest['report_date']}**

### 1. Overall Worker Population Movement
* **Previous Total:** {prev['total_workers']:,}
* **Current Total:** **{latest['total_workers']:,}**
* **Net Change ($\Delta$):** **{sign}{worker_diff:,} ({sign}{worker_pct:.2f}%)**

---

### 2. Destination Country Shifts ($\Delta$)
"""
    if top_gainers:
        report_md += "\n**Top Growing Destinations:**\n"
        for g in top_gainers:
            report_md += f"* **{g['country']}:** +{g['diff']:,} ({g['prev_count']:,} $\\rightarrow$ {g['curr_count']:,})\n"
            
    if top_losers:
        report_md += "\n**Top Declining Destinations:**\n"
        for l in top_losers:
            report_md += f"* **{l['country']}:** {l['diff']:,} ({l['prev_count']:,} $\\rightarrow$ {l['curr_count']:,})\n"

    report_md += "\n---\n\n### 3. Origin Province Shifts ($\Delta$)\n"
    if top_prov_gainers:
        report_md += "\n**Top Province Increases:**\n"
        for pg in top_prov_gainers:
            report_md += f"* **{pg['province']}:** +{pg['diff']:,} ({pg['prev_count']:,} $\\rightarrow$ {pg['curr_count']:,})\n"
            
    if top_prov_losers:
        report_md += "\n**Top Province Decreases:**\n"
        for pl in top_prov_losers:
            report_md += f"* **{pl['province']}:** {pl['diff']:,} ({pl['prev_count']:,} $\\rightarrow$ {pl['curr_count']:,})\n"
            
    return report_md
