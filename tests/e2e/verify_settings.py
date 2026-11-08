from playwright.sync_api import sync_playwright
import os

def run_cuj(page):
    page.goto("http://127.0.0.1:8000")
    page.wait_for_timeout(1000)

    # Click Settings Nav
    page.get_by_role("button", name="Settings").click()
    page.wait_for_timeout(2000)

    # Refresh Diagnostics
    page.get_by_role("button", name="Refresh Diagnostics").click()
    page.wait_for_timeout(2000)

    # Take screenshot
    page.screenshot(path="/home/jules/verification/screenshots/settings_fixed.png")
    page.wait_for_timeout(1000)

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            record_video_dir="/home/jules/verification/videos"
        )
        page = context.new_page()
        try:
            run_cuj(page)
        finally:
            context.close()
            browser.close()
