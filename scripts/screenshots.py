"""Capture dashboard screenshots for the README (requires running server)."""

import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
RUN_ID = sys.argv[1] if len(sys.argv) > 1 else "run_27483f40"

PAGES = [
    ("#/", "dashboard_home.png", 2.5),
    (f"#/run/{RUN_ID}", "dashboard_run.png", 4.0),
    (f"#/run/{RUN_ID}/report", "dashboard_report.png", 3.0),
]

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1560, "height": 980}, device_scale_factor=1.5)
    for route, name, wait in PAGES:
        page.goto(f"{BASE}/{route}")
        page.wait_for_load_state("networkidle")
        time.sleep(wait)  # let polling cycles populate charts
        page.screenshot(path=f"docs/img/{name}", full_page=False)
        print("captured", name)
    browser.close()
