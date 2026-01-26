import asyncio
from playwright.async_api import async_playwright


async def main() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        page = await browser.new_page()
        await page.goto(
            "https://outagemap.duke-energy.com/#/current-outages/ncsc?jurisdiction=DEP",
            wait_until="domcontentloaded",
        )
        await page.wait_for_timeout(4000)
        print("URL:", page.url)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
