import asyncio
from playwright.async_api import async_playwright


async def dump_links(page, *, selector: str = "a") -> None:
    link_locator = page.locator(selector)
    count = await link_locator.count()
    print(f"links: count={count}")
    for idx in range(count):
        handle = link_locator.nth(idx)
        text = (await handle.inner_text()).strip()
        href = await handle.get_attribute("href")
        print(f"  [{idx}] text={text!r} href={href!r}")


async def main() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        page = await browser.new_page()
        page.on(
            "requestfinished",
            lambda request: print("REQUEST:", request.url)
            if "outage-maps" in request.url.lower()
            else None,
        )
        page.on(
            "console",
            lambda message: print("CONSOLE:", message.text)
        )
        await page.goto("https://outagemap.duke-energy.com/#/current-outages/ncsc", wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)

        print("Initial state:")
        await dump_links(page)

        progress_link = page.locator("a:has-text('Duke Energy Progress')").first
        if await progress_link.count():
            print("Clicking Duke Energy Progress card...")
            await progress_link.click()
            await page.wait_for_timeout(2000)
            print("URL after DEP click:", page.url)
            storage = await page.evaluate("() => ({local: window.localStorage, session: window.sessionStorage})")
            print("Storage after DEP click:", storage)
            print("After clicking DEP:")
            await dump_links(page)
            progress_text_nodes = page.locator("text='Duke Energy Progress'")
            if await progress_text_nodes.count():
                texts = await progress_text_nodes.all_inner_texts()
                print("Progress text nodes after click:", texts)

        view_link = page.locator("a:has-text('View outage map')").first
        if await view_link.count():
            print("Clicking View outage map...")
            await view_link.click()
            await page.wait_for_timeout(2000)
            print("URL after view click:", page.url)
            print("After clicking view link:")
            await dump_links(page)

        menu_button = page.locator("button[aria-label='Menu Button']").first
        if await menu_button.count():
            print("Opening menu...")
            await menu_button.click()
            await page.wait_for_timeout(1000)
            print("Menu links:")
            await dump_links(page)
            menu_links = page.locator("a").filter(has_text="Duke Energy Progress")
            if await menu_links.count():
                print("Clicking Progress link from menu...")
                await menu_links.first.click()
                await page.wait_for_timeout(2000)
                print("URL after menu selection:", page.url)

        progress_buttons = page.locator("button", has_text="Duke Energy Progress")
        progress_divs = page.locator("div", has_text="Duke Energy Progress")
        for label, locator in (("button", progress_buttons), ("div", progress_divs)):
            count = await locator.count()
            if count:
                print(f"Found {count} {label}(s) with Progress text")
            nodes = await page.evaluate(
                "() => Array.from(document.querySelectorAll('*'))\n"
                "  .filter(el => (el.innerText || '').includes('Duke Energy'))\n"
                "  .slice(0, 30)\n"
                "  .map(el => ({tag: el.tagName, id: el.id, classes: el.className, text: el.innerText.trim().slice(0, 160)}))"
            )
            print("Nodes mentioning Duke Energy (first 30):")
            for idx, node in enumerate(nodes):
                print(
                    f"  [{idx}] tag={node['tag']} id={node['id']!r} classes={node['classes']!r} text={node['text']!r}"
                )
            class_hits = await page.evaluate(
                "() => Array.from(document.querySelectorAll('*'))\n"
                "  .map(el => el.className || '')\n"
                "  .filter(name => name.includes('jurisdiction'))\n"
                "  .slice(0, 30)"
            )
            print("Classes containing 'jurisdiction':", class_hits)
        await page.wait_for_timeout(2000)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
