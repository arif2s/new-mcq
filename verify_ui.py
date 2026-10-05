from playwright.sync_api import sync_playwright

def run_cuj(page):
    page.goto("http://localhost:8000")
    page.wait_for_timeout(2000)

    page.click("button:has-text('Process (Offline Preparation)')", timeout=5000)
    page.wait_for_timeout(2000)

    # Check if a single click toggles it
    page.click("text=General Medical Concept")
    page.wait_for_timeout(500)
    page.screenshot(path="/home/jules/verification/screenshots/single_click.png")

    # Double click
    page.dblclick("text=General Medical Concept")
    page.wait_for_timeout(500)
    page.screenshot(path="/home/jules/verification/screenshots/double_click.png")

    # Enter new name and press enter
    page.fill('input[type="text"]', "Boolean")
    page.press('input[type="text"]', 'Enter')
    page.wait_for_timeout(500)
    page.screenshot(path="/home/jules/verification/screenshots/renamed.png")

    # Finally confirm
    page.click("button:has-text('Confirm & Queue')")
    page.wait_for_timeout(1000)

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        try:
            run_cuj(page)
        except Exception as e:
            print(f"Error: {e}")
        finally:
            context.close()
            browser.close()
