"""Парсер Яндекс.Карт: ищет организации по сфере и городу."""
import asyncio
from urllib.parse import quote
from playwright.async_api import async_playwright


SEARCH_URL = "https://yandex.ru/maps/?mode=search&text={query}"

CARD_SELECTOR = ".search-snippet-view"
TITLE_SELECTOR = ".search-business-snippet-view__title, [class*='search-business-snippet-view__title']"
ADDR_SELECTOR = ".search-business-snippet-view__address, [class*='search-business-snippet-view__address']"
SCROLL_BOX = ".scroll__container"

PHONE_SELECTORS = [
    "[class*='orgpage-phones-view__phone-number']",
    "[class*='orgpage-phones-view__phone']",
    "[class*='phones-view__phone-number']",
    "a[href^='tel:']",
]
SITE_SELECTORS = [
    "a[class*='business-urls-view__text']",
    "[class*='business-urls-view'] a",
    "a[class*='action-button-view'][href^='http']:not([href*='yandex.'])",
]


async def _extract_card_detail(page, card, idx, log):
    """Кликает на карточку и собирает данные из боковой панели."""
    try:
        title_el = await card.query_selector(TITLE_SELECTOR)
        if not title_el:
            return None
        name = (await title_el.inner_text()).strip()

        addr_el = await card.query_selector(ADDR_SELECTOR)
        address = (await addr_el.inner_text()).strip() if addr_el else ""

        # Клик открывает детальную панель
        await title_el.scroll_into_view_if_needed()
        await title_el.click()
        await page.wait_for_timeout(900)

        phone = ""
        for sel in PHONE_SELECTORS:
            el = await page.query_selector(sel)
            if el:
                txt = (await el.inner_text()).strip()
                if not txt:
                    txt = (await el.get_attribute("href") or "").replace("tel:", "")
                if txt:
                    phone = txt
                    break

        site = ""
        for sel in SITE_SELECTORS:
            el = await page.query_selector(sel)
            if el:
                href = await el.get_attribute("href") or ""
                text = (await el.inner_text()).strip()
                site = href or text
                if site:
                    break

        return {
            "name": name,
            "address": address,
            "phone": phone,
            "site": site,
            "has_site": bool(site),
        }
    except Exception as e:
        log(f"[{idx}] ошибка карточки: {e}")
        return None


async def search_async(category, city, max_results=80, headless=True,
                       only_without_site=True, log=print, on_result=None,
                       stop_flag=None):
    """Основная корутина парсинга."""
    query = f"{category} {city}".strip()
    url = SEARCH_URL.format(query=quote(query))
    log(f"Запрос: {query}")
    log(f"URL: {url}")

    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            locale="ru-RU",
            viewport={"width": 1400, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log(f"Не удалось открыть страницу: {e}")
            await browser.close()
            return results

        try:
            await page.wait_for_selector(CARD_SELECTOR, timeout=15000)
        except Exception:
            log("Карточки не найдены — возможно, изменилась вёрстка или капча.")
            await browser.close()
            return results

        # Прокрутка для подгрузки карточек
        log("Прокручиваю список результатов...")
        prev = 0
        stable = 0
        while True:
            if stop_flag and stop_flag():
                log("Остановлено пользователем (прокрутка).")
                break
            cards = await page.query_selector_all(CARD_SELECTOR)
            cur = len(cards)
            log(f"Найдено карточек: {cur}")
            if cur >= max_results:
                break
            if cur == prev:
                stable += 1
                if stable >= 3:
                    break
            else:
                stable = 0
            prev = cur
            if cards:
                try:
                    await cards[-1].scroll_into_view_if_needed()
                except Exception:
                    pass
            await page.wait_for_timeout(1500)

        cards = await page.query_selector_all(CARD_SELECTOR)
        cards = cards[:max_results]
        log(f"Обрабатываю {len(cards)} карточек...")

        for i, card in enumerate(cards, 1):
            if stop_flag and stop_flag():
                log("Остановлено пользователем.")
                break
            data = await _extract_card_detail(page, card, i, log)
            if not data:
                continue
            data["category"] = category
            data["city"] = city
            if only_without_site and data["has_site"]:
                log(f"[{i}/{len(cards)}] {data['name']} — есть сайт, пропуск")
                continue
            results.append(data)
            log(f"[{i}/{len(cards)}] + {data['name']}")
            if on_result:
                on_result(data)

        await browser.close()
    log(f"Готово. Итого: {len(results)} организаций.")
    return results


def search(category, city, **kwargs):
    """Синхронная обёртка над search_async."""
    return asyncio.run(search_async(category, city, **kwargs))
