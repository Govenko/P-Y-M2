import asyncio
import random
import re
import csv
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

class YandexMapsUltimateParser:
    def __init__(self):
        self.results = []
        self.base_url = "https://yandex.ru"

    async def random_delay(self, min_sec=2, max_sec=5):
        await asyncio.sleep(random.uniform(min_sec, max_sec))

    async def infinite_scroll(self, page, max_scrolls=50):
        previous_count = 0
        same_count = 0
        for i in range(max_scrolls):
            await page.evaluate("window.scrollBy(0, 3000)")
            await self.random_delay(1.5, 2.5)
            current_count = await page.locator('a[href*="/maps/org/"]').count()
            print(f"  Прокрутка {i+1}: найдено {current_count} ссылок")
            if current_count == previous_count:
                same_count += 1
                if same_count >= 3:
                    break
            else:
                same_count = 0
                previous_count = current_count
        print(f"  Итого ссылок: {previous_count}")

    async def collect_links(self, query: str, city: str, max_places: int):
        search_url = f"https://yandex.ru/maps/?text={query}+{city}"
        print(f"[INFO] Открываю поиск: {search_url}")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            page = await browser.new_page()
            await page.goto(search_url, wait_until='networkidle', timeout=60000)
            await self.random_delay(4, 7)
            await self.infinite_scroll(page, max_scrolls=40)

            elements = await page.locator('a[href*="/maps/org/"]').all()
            links = set()
            for el in elements:
                href = await el.get_attribute('href')
                if not href:
                    continue
                if any(skip in href for skip in ['/gallery/', '/reviews/', '/photo/', '/info/']):
                    continue
                if href.startswith('/'):
                    href = self.base_url + href
                href = href.split('?')[0].rstrip('/')
                links.add(href)
            await browser.close()
            print(f"[INFO] Уникальных ссылок: {len(links)}")
            return list(links)[:max_places]

    async def is_real_captcha(self, page):
        iframe = await page.locator('iframe[src*="captcha"], iframe[src*="smartcaptcha"]').count()
        if iframe > 0:
            return True
        text = await page.locator('text=Подтвердите, что вы не робот').count()
        if text > 0:
            return True
        return False

    async def click_phone_button(self, page):
        button_selectors = [
            "button:has-text('Показать телефон')",
            "button:has-text('Показать')",
            "button[class*='phone']",
            ".business-phone button",
            "[class*='phone-button']",
            "button[data-testid='phone-button']"
        ]
        for selector in button_selectors:
            try:
                button = await page.locator(selector).first
                if await button.count() > 0:
                    await button.scroll_into_view_if_needed()
                    await self.random_delay(0.5, 1)
                    await button.click()
                    print("  [✓] Нажата кнопка телефона")
                    await self.random_delay(1.5, 2.5)
                    return True
            except Exception:
                continue
        return False

    async def get_phone_from_page(self, page):
        try:
            tel_elem = await page.locator('a[href^="tel:"]').first
            if await tel_elem.count() > 0:
                phone = await tel_elem.text_content()
                if phone and len(phone) >= 6:
                    return phone.strip()
        except:
            pass
        html = await page.content()
        soup = BeautifulSoup(html, 'html.parser')
        for el in soup.find_all(class_=re.compile(r'phone', re.I)):
            text = el.text.strip()
            if re.search(r'[\+\d][\d\s\-\(\)]{5,}', text):
                return text
        for el in soup.find_all(attrs={"data-phone": True}):
            phone = el['data-phone']
            if phone:
                return phone
        phone_pattern = re.compile(r'(\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}')
        for text in soup.stripped_strings:
            match = phone_pattern.search(text)
            if match:
                return match.group(0)
        return ""

    async def parse_organization(self, url: str, query: str, city: str):
        print(f"[INFO] Парсинг: {url}")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            page = await browser.new_page()
            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=60000)
                await self.random_delay(2, 4)

                if await self.is_real_captcha(page):
                    print("\n⚠️  Капча! Решите в браузере и нажмите Enter...")
                    input()
                    await page.reload()
                    await self.random_delay(3, 5)

                await self.click_phone_button(page)
                phone = await self.get_phone_from_page(page)
                print(f"  Телефон: {phone if phone else 'не найден'}")

                html = await page.content()
                soup = BeautifulSoup(html, 'html.parser')

                data = {
                    "ID организации": re.search(r'/(\d+)/?$', url).group(1) if re.search(r'/(\d+)/?$', url) else "",
                    "Название": (soup.find('h1').text.strip() if soup.find('h1') else ""),
                    "Адрес": (soup.find('div', class_=re.compile(r'address', re.I)).text.strip() if soup.find('div', class_=re.compile(r'address', re.I)) else ""),
                    "Координаты": "",
                    "Категория": "",
                    "Рейтинг": "",
                    "Отзывов": "",
                    "Телефоны": phone,
                    "Сайты": "",
                    "Соцсети": "",
                    "Ссылка": url
                }

                # Координаты
                coord_match = re.search(r'"coordinates"\s*:\s*\[([\d\.]+),\s*([\d\.]+)\]', html)
                if not coord_match:
                    coord_match = re.search(r'point\s*:\s*\[([\d\.]+),\s*([\d\.]+)\]', html)
                if coord_match:
                    lon, lat = coord_match.group(1), coord_match.group(2)
                    data["Координаты"] = f"{lat}, {lon}"

                # Категория
                cat = soup.find('span', class_=re.compile(r'category', re.I)) or soup.find('a', class_=re.compile(r'category', re.I))
                if cat:
                    data["Категория"] = cat.text.strip()

                # Рейтинг
                rating = ""
                rating_selectors = [
                    'span[class*="rating-value"]',
                    'span[class*="business-rating"]',
                    'div[class*="rating"] span',
                    '[aria-label*="Рейтинг"]',
                    'span[class*="Rating"]'
                ]
                for selector in rating_selectors:
                    rating_elem = soup.select_one(selector)
                    if rating_elem:
                        rating = rating_elem.text.strip()
                        if "Рейтинг" in rating:
                            match = re.search(r'([\d,\.]+)', rating)
                            if match:
                                rating = match.group(1)
                        if rating and any(c.isdigit() for c in rating):
                            break
                if not rating:
                    script_text = ' '.join([script.text for script in soup.find_all('script') if 'rating' in script.text.lower()])
                    match = re.search(r'"rating"\s*:\s*([\d,\.]+)', script_text)
                    if match:
                        rating = match.group(1)
                data["Рейтинг"] = rating
                print(f"  Рейтинг: {rating if rating else 'не найден'}")

                # Отзывы
                reviews = ""
                review_selectors = [
                    'a[href*="reviews"] span[class*="count"]',
                    'a[class*="reviews"] span',
                    'span[class*="reviewsCount"]',
                    'a[href*="reviews"] .count'
                ]
                for selector in review_selectors:
                    reviews_elem = soup.select_one(selector)
                    if reviews_elem:
                        reviews = reviews_elem.text.strip()
                        if reviews.isdigit() or re.search(r'\d+', reviews):
                            break
                if not reviews:
                    rev_link = soup.find('a', href=re.compile(r'reviews'))
                    if rev_link:
                        txt = rev_link.text.strip()
                        nums = re.findall(r'\d+', txt)
                        if nums:
                            reviews = nums[0]
                if not reviews:
                    script_text = ' '.join([script.text for script in soup.find_all('script') if 'reviewsCount' in script.text])
                    match = re.search(r'"reviewsCount"\s*:\s*(\d+)', script_text)
                    if match:
                        reviews = match.group(1)
                data["Отзывов"] = reviews
                print(f"  Отзывов: {reviews if reviews else 'не найдено'}")

                # Сайты и соцсети
                sites, socials = set(), set()
                for link in soup.find_all('a', href=re.compile(r'^https?://')):
                    href = link.get('href')
                    if 'yandex.ru' in href:
                        continue
                    if any(domain in href for domain in ['t.me', 'wa.me', 'vk.com', 'youtube.com', 'telegram', 'viber', 'whatsapp']):
                        socials.add(href)
                    else:
                        sites.add(href)
                data["Сайты"] = ", ".join(sites)
                data["Соцсети"] = ", ".join(socials)

                await browser.close()
                return data

            except Exception as e:
                print(f"[ERROR] {url}: {e}")
                await browser.close()
                return None

    async def run(self, query: str, city: str, max_places: int):
        print("[START] Парсер с поддержкой рейтинга и отзывов")
        links = await self.collect_links(query, city, max_places)
        if not links:
            print("Нет ссылок.")
            return
        for idx, link in enumerate(links, 1):
            print(f"\n--- [{idx}/{len(links)}] ---")
            data = await self.parse_organization(link, query, city)
            if data:
                self.results.append(data)
                print(f"  ✓ {data['Название']} (тел: {data['Телефоны'][:30] if data['Телефоны'] else 'нет'}, рейтинг: {data['Рейтинг']}, отзывов: {data['Отзывов']})")
            await self.random_delay(5, 8)
        self.save_results(query, city)

    def save_results(self, query: str, city: str):
        if not self.results:
            print("Нет данных.")
            return
        filename = f"{query}_{city}_final.csv"
        with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
            fieldnames = ["ID организации", "Название", "Адрес", "Координаты", "Категория",
                          "Рейтинг", "Отзывов", "Телефоны", "Сайты", "Соцсети", "Ссылка"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.results)
        print(f"\n✅ Сохранено {len(self.results)} записей в {filename}")

async def main():
    parser = YandexMapsUltimateParser()
    query = input("Поисковый запрос: ").strip()
    city = input("Город: ").strip()
    max_places = int(input("Максимум организаций (0 - все): ") or "10")
    if max_places == 0:
        max_places = 200
    await parser.run(query, city, max_places)

if __name__ == "__main__":
    asyncio.run(main())
