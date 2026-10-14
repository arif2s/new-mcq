from playwright.sync_api import sync_playwright

def test_theme_toggle():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://localhost:8000")
        page.wait_for_timeout(1000)

        # Light mode screenshot
        page.screenshot(path="/home/jules/verification/screenshots/light_mode.png")

        # Toggle to dark mode
        page.click("#theme-toggle")
        page.wait_for_timeout(1000)

        # Dark mode screenshot
        page.screenshot(path="/home/jules/verification/screenshots/dark_mode.png")

        browser.close()

if __name__ == "__main__":
    test_theme_toggle()
