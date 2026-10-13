import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()

        # Add event listener for console logs
        page.on("console", lambda msg: print(f"Browser console: {msg.text}"))
        page.on("pageerror", lambda err: print(f"Browser error: {err}"))

        await page.goto('http://127.0.0.1:8000')
        await page.wait_for_timeout(1000)
        print("Clicking Quiz Engine...")
        await page.click('button[data-target="quiz-view"]')
        await page.wait_for_timeout(1000)

        await page.screenshot(path='/home/jules/verification/screenshots/verification6.png')
        await browser.close()

asyncio.run(run())
