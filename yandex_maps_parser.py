#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yandex Maps Parser - Обходной парсер с двухэтапной стратегией сбора данных.

АРХИТЕКТУРА И СТРАТЕГИЯ ОБХОДА ЛИМИТОВ:
=========================================
Проблема: Яндекс.Карты ограничивают выдачу ~500 результатами на один поисковый запрос.

Решение (Двухэтапный подход):
1. ЭТАП 1 - Сбор рубрикатора города:
   - Переходим на Яндекс.Карты по выбранному городу
   - Открываем фильтр/рубрикатор и извлекаем ВСЕ доступные рубрики для этого города
   - Сохраняем список рубрик (может быть от 50 до 500+ категорий)

2. ЭТАП 2 - Поиск по каждой рубрике отдельно:
   - Итеративно выполняем поиск по КАЖДОЙ найденной рубрике
   - Каждый запрос возвращает до 500 результатов
   - Таким образом обходим лимит: 100 рубрик × 500 org = до 50,000 организаций

Преимущества:
- Не требует платных прокси или API капчи
- Использует playwright-stealth для базовой маскировки под человека
- Человеческие задержки между действиями (random 2-6 сек)
- Ручное вмешательство при капче (браузер остаётся открытым)
- Построчное сохранение в CSV для защиты от потери данных
- Пропуск уже собранных организаций по URL

Автор: Senior Python Developer (Web Scraping Expert)
"""

import asyncio
import csv
import os
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Set, Dict, List, Any

from dotenv import load_dotenv
from loguru import logger
from playwright.async_api import async_playwright, Page, BrowserContext
from playwright_stealth import stealth_async

# ─────────────────────────────────────────────────────────────────────────────
# КОНФИГУРАЦИЯ (можно переопределить через .env)
# ─────────────────────────────────────────────────────────────────────────────

load_dotenv()

CITY = os.getenv("CITY", "Москва")
MAX_ORGANIZATIONS = int(os.getenv("MAX_ORGANIZATIONS", "0"))
OUTPUT_CSV = os.getenv("OUTPUT_CSV", "yandex_maps_result.csv")
MIN_DELAY = float(os.getenv("MIN_DELAY", "2"))
MAX_DELAY = float(os.getenv("MAX_DELAY", "6"))
DEBUG_MODE = os.getenv("DEBUG_MODE", "True").lower() == "true"
PAGE_TIMEOUT = int(os.getenv("PAGE_TIMEOUT", "60000"))
MAX_SCROLLS_RUBRICS = int(os.getenv("MAX_SCROLLS_RUBRICS", "30"))
MAX_SCROLLS_SEARCH = int(os.getenv("MAX_SCROLLS_SEARCH", "50"))

# Базовые колонки CSV
BASE_COLUMNS = [
    "name", "region", "city", "address", "section", "subsections", "rubrics",
    "phones", "mobiles", "email", "website", "vk", "telegram", "instagram", "max",
    "latitude", "longitude", "working_hours", "logo_url", "gallery_images"
]

# Динамические колонки для особенностей (features) - заполняются в процессе
dynamic_features_columns: List[str] = []

# Ссылка на Яндекс.Карты
YANDEX_MAPS_BASE = "https://yandex.ru/maps"


# ─────────────────────────────────────────────────────────────────────────────
# МОДЕЛИ ДАННЫХ
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Organization:
    """Модель организации со всеми полями."""
    name: str = ""
    region: str = ""
    city: str = ""
    address: str = ""
    section: str = ""
    subsections: str = ""
    rubrics: str = ""
    phones: str = ""
    mobiles: str = ""
    email: str = ""
    website: str = ""
    vk: str = ""
    telegram: str = ""
    instagram: str = ""
    max_social: str = ""
    latitude: str = ""
    longitude: str = ""
    working_hours: str = ""
    logo_url: str = ""
    gallery_images: str = ""
    features: Dict[str, str] = field(default_factory=dict)
    org_url: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертирует организацию в словарь для CSV."""
        result = {
            "name": self.name,
            "region": self.region,
            "city": self.city,
            "address": self.address,
            "section": self.section,
            "subsections": self.subsections,
            "rubrics": self.rubrics,
            "phones": self.phones,
            "mobiles": self.mobiles,
            "email": self.email,
            "website": self.website,
            "vk": self.vk,
            "telegram": self.telegram,
            "instagram": self.instagram,
            "max": self.max_social,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "working_hours": self.working_hours,
            "logo_url": self.logo_url,
            "gallery_images": self.gallery_images,
        }
        # Добавляем динамические колонки особенностей
        for feat in dynamic_features_columns:
            result[feat] = self.features.get(feat, "")
        return result
    
    @property
    def unique_key(self) -> str:
        """Уникальный ключ организации (URL)."""
        return self.org_url


# ─────────────────────────────────────────────────────────────────────────────
# УТИЛИТЫ
# ─────────────────────────────────────────────────────────────────────────────

def setup_logger():
    """Настраивает логгер loguru."""
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>",
        level="INFO"
    )
    logger.add(
        "parser.log",
        rotation="10 MB",
        retention="7 days",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        level="DEBUG"
    )


async def human_delay(min_sec: float = MIN_DELAY, max_sec: float = MAX_DELAY):
    """Человеческая задержка между действиями."""
    delay = random.uniform(min_sec, max_sec)
    await asyncio.sleep(delay)


def clean_text(text: Optional[str]) -> str:
    """Очищает текст от лишних пробелов."""
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text.strip())


def extract_coordinates(html: str) -> tuple:
    """Извлекает координаты из HTML страницы."""
    lat, lon = "", ""
    
    # Паттерн 1: "coordinates": [lon, lat]
    match = re.search(r'"coordinates"\s*:\s*\[([\d\.\-]+),\s*([\d\.\-]+)\]', html)
    if match:
        lon, lat = match.group(1), match.group(2)
    
    # Паттерн 2: point: [lon, lat]
    if not lat:
        match = re.search(r'point\s*:\s*\[([\d\.\-]+),\s*([\d\.\-]+)\]', html)
        if match:
            lon, lat = match.group(1), match.group(2)
    
    # Паттерн 3: ll=lon,lat в URL
    if not lat:
        match = re.search(r'll=([\d\.\-]+),([\d\.\-]+)', html)
        if match:
            lon, lat = match.group(1), match.group(2)
    
    return lat, lon


def parse_phone(phone_str: str) -> str:
    """Нормализует телефонный номер."""
    if not phone_str:
        return ""
    
    # Извлекаем цифры
    digits = re.sub(r'\D', '', phone_str)
    
    # Обрабатываем российские номера
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    elif len(digits) == 10:
        digits = '7' + digits
    
    if len(digits) == 11 and digits.startswith('7'):
        return f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    
    return clean_text(phone_str)


# ─────────────────────────────────────────────────────────────────────────────
# ПАРСЕР РУБРИК (ЭТАП 1)
# ─────────────────────────────────────────────────────────────────────────────

class RubricParser:
    """Парсер для сбора всех рубрик города."""
    
    def __init__(self, page: Page):
        self.page = page
    
    async def collect_rubrics(self, city: str) -> List[str]:
        """
        Собирает все доступные рубрики для указанного города.
        
        Логика:
        1. Переходим на карты по городу
        2. Ищем кнопку/filter рубрикатора
        3. Раскрываем все категории
        4. Собираем названия рубрик
        """
        logger.info(f"📋 ЭТАП 1: Сбор рубрик для города '{city}'")
        
        # Формируем URL для города
        city_encoded = city.replace(' ', '+')
        url = f"{YANDEX_MAPS_BASE}/?ll=37.617635%2C55.755814&z=10&text={city_encoded}"
        
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            await human_delay(3, 5)
            
            # Принимаем cookies если есть
            await self._accept_cookies()
            
            # Ждём загрузки интерфейса
            await human_delay(2, 4)
            
            # Пробуем найти и открыть рубрикатор
            rubrics = await self._extract_rubrics_from_page()
            
            if not rubrics:
                # Альтернативный способ: поиск через категорию "Все"
                logger.warning("Не удалось найти рубрикатор стандартным способом, пробуем альтернативный...")
                rubrics = await self._extract_rubrics_alternative(city)
            
            logger.success(f"✅ Найдено {len(rubrics)} рубрик для города '{city}'")
            return list(set(rubrics))  # Убираем дубликаты
            
        except Exception as e:
            logger.error(f"Ошибка при сборе рубрик: {e}")
            # Возвращаем базовый список популярных рубрик как fallback
            return self._get_fallback_rubrics()
    
    async def _accept_cookies(self):
        """Принимает cookies соглашения."""
        try:
            accept_buttons = [
                "Принять все",
                "Allow all",
                "OK",
                "Хорошо"
            ]
            for btn_text in accept_buttons:
                button = self.page.get_by_text(btn_text, exact=False).first
                if await button.count() > 0:
                    await button.click(timeout=2000)
                    await human_delay(1, 2)
                    break
        except Exception:
            pass
    
    async def _extract_rubrics_from_page(self) -> List[str]:
        """Извлекает рубрики со страницы."""
        rubrics = []
        
        try:
            # Ищем элементы рубрик в фильтре/категориях
            rubric_selectors = [
                ".categories-view__category",
                "[class*='category'] a",
                ".rubric-item",
                "[data-testid*='category']",
                "a[href*='/category/']"
            ]
            
            for selector in rubric_selectors:
                elements = await self.page.query_selector_all(selector)
                for el in elements:
                    try:
                        text = await el.inner_text()
                        text = clean_text(text)
                        if text and len(text) < 100:
                            rubrics.append(text)
                    except Exception:
                        continue
            
            # Также собираем из URL категорий
            links = await self.page.query_selector_all("a[href*='/category/']")
            for link in links:
                href = await link.get_attribute("href") or ""
                # Извлекаем название категории из URL
                match = re.search(r'/category/([^/?]+)', href)
                if match:
                    cat_name = match.group(1).replace('-', ' ').title()
                    if cat_name and len(cat_name) < 100:
                        rubrics.append(cat_name)
            
        except Exception as e:
            logger.debug(f"Ошибка извлечения рубрик: {e}")
        
        return rubrics
    
    async def _extract_rubrics_alternative(self, city: str) -> List[str]:
        """Альтернативный метод сбора рубрик через поиск популярных категорий."""
        rubrics = []
        
        popular_categories = [
            "рестораны", "кафе", "магазины", "аптеки", "больницы",
            "школы", "детские сады", "банки", "отели", "автосервисы",
            "салоны красоты", "фитнес", "парикмахерские", "стоматологии",
            "продукты", "стройматериалы", "автомойки", "шиномонтаж",
            "ветклиники", "ветеринары", "бассейны", "сауны", "кинотеатры",
            "театры", "музеи", "парки", "скверы", "торговые центры",
            "супермаркеты", "рынки", "заправки", "гостиницы", "хостелы",
            "кофейни", "пекарни", "пиццерии", "суши", "бургеры",
            "бары", "пабы", "ночные клубы", "боулинг", "бильярд",
            "квесты", "развлечения", "детские площадки", "кружки",
            "секции", "языковые школы", "репетиторы", "курсы",
            "юристы", "нотариусы", "бухгалтеры", "страховые",
            "турфирмы", "авиакассы", "такси", "грузоперевозки",
            "эвакуаторы", "ремонт телефонов", "ремонт компьютеров",
            "химчистки", "прачечные", "ателье", "ремонт обуви",
            "пункты выдачи", "почта", "курьерская доставка",
            "фотостудии", "видеосъемка", "организация праздников",
            "цветы", "подарки", "ювелирные", "часы", "оптика",
            "зоомагазины", "товары для животных", "груминг",
            "бани", "солярии", "массаж", "косметология", "диетологи",
            "психологи", "логопеды", "массажисты", "мануальные терапевты",
            "наркология", "гирудотерапия", "физиотерапия", "узист",
            "гинекология", "урология", "проктология", "лор", "окулист",
            "неврология", "кардиология", "эндокринология", "гастроэнтерология",
            "пульмонология", "ревматология", "аллергология", "иммунология",
            "онкология", "маммология", "дерматология", "трихология",
            "андрология", "сексология", "генетика", "репродуктология",
            "педиатрия", "неонатология", "детская хирургия", "детская стоматология"
        ]
        
        logger.info(f"Используем альтернативный метод с {len(popular_categories)} популярными категориями")
        return popular_categories
    
    def _get_fallback_rubrics(self) -> List[str]:
        """Возвращает базовый список рубрик при ошибке."""
        return [
            "рестораны", "кафе", "магазины", "аптеки", "больницы",
            "банки", "отели", "автосервисы", "салоны красоты", "фитнес"
        ]


# ─────────────────────────────────────────────────────────────────────────────
# ПАРСЕР ОРГАНИЗАЦИЙ (ЭТАП 2)
# ─────────────────────────────────────────────────────────────────────────────

class OrganizationParser:
    """Парсер для сбора данных об организациях."""
    
    def __init__(self, page: Page, city: str):
        self.page = page
        self.city = city
        self.seen_urls: Set[str] = set()
        self.csv_file: Optional[Any] = None
        self.csv_writer: Optional[csv.DictWriter] = None
    
    async def search_by_rubric(self, rubric: str, max_results: int = 500) -> List[Organization]:
        """
        Выполняет поиск по конкретной рубрике и собирает организации.
        
        Args:
            rubric: Название рубрики для поиска
            max_results: Максимум результатов (по умолчанию 500 - лимит Яндекса)
        
        Returns:
            Список найденных организаций
        """
        logger.info(f"🔍 Поиск по рубрике: '{rubric}'")
        
        organizations: List[Organization] = []
        
        try:
            # Формируем поисковый запрос
            query = f"{rubric} {self.city}".strip()
            url = f"{YANDEX_MAPS_BASE}/?text={query.replace(' ', '+')}"
            
            await self.page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            await human_delay(3, 5)
            
            # Принимаем cookies
            await self._accept_cookies()
            await human_delay(2, 3)
            
            # Проверяем на капчу
            if await self._check_captcha():
                logger.warning("⚠️  Обнаружена капча! Решите её вручную в браузере...")
                await self._wait_for_captcha_solve()
            
            # Скроллим для подгрузки результатов
            await self._scroll_results(MAX_SCROLLS_SEARCH)
            await human_delay(2, 3)
            
            # Собираем ссылки на организации
            org_links = await self._collect_org_links(max_results)
            logger.info(f"Найдено {len(org_links)} ссылок на организации")
            
            # Парсим каждую организацию
            for idx, org_url in enumerate(org_links, 1):
                if org_url in self.seen_urls:
                    logger.debug(f"Пропускаем уже собранную: {org_url}")
                    continue
                
                if MAX_ORGANIZATIONS > 0 and len(self.seen_urls) >= MAX_ORGANIZATIONS:
                    logger.info("Достигнут лимит организаций")
                    break
                
                logger.info(f"[{idx}/{len(org_links)}] Парсинг организации: {org_url}")
                
                try:
                    org = await self._parse_organization_page(org_url, rubric)
                    if org:
                        organizations.append(org)
                        self.seen_urls.add(org_url)
                        # Сохраняем сразу после успешного парсинга
                        self._save_to_csv(org)
                        logger.success(f"✓ Сохранено: {org.name}")
                except Exception as e:
                    logger.error(f"Ошибка парсинга организации {org_url}: {e}")
                    continue
                
                # Человеческая задержка между организациями
                await human_delay(3, 6)
            
        except Exception as e:
            logger.error(f"Ошибка при поиске по рубрике '{rubric}': {e}")
        
        return organizations
    
    async def _accept_cookies(self):
        """Принимает cookies."""
        try:
            buttons = ["Принять все", "Allow all", "OK"]
            for btn_text in buttons:
                button = self.page.get_by_text(btn_text, exact=False).first
                if await button.count() > 0:
                    await button.click(timeout=2000)
                    break
        except Exception:
            pass
    
    async def _check_captcha(self) -> bool:
        """Проверяет наличие капчи."""
        captcha_indicators = [
            "smartcaptcha",
            "captcha",
            "Подтвердите, что вы не робот",
            "Ой!"
        ]
        
        page_content = await self.page.content()
        return any(indicator.lower() in page_content.lower() for indicator in captcha_indicators)
    
    async def _wait_for_captcha_solve(self):
        """Ждёт пока пользователь решит капчу вручную."""
        logger.info("💡 ОТКРОЙТЕ БРАУЗЕР И РЕШИТЕ КАПЧУ ВРУЧНУЮ. Парсер продолжит автоматически после решения.")
        
        # Ждём до 5 минут, проверяя каждые 5 секунд
        for _ in range(60):
            await asyncio.sleep(5)
            if not await self._check_captcha():
                logger.success("✅ Капча решена! Продолжаем работу...")
                return
        
        logger.warning("⏰ Таймаут ожидания капчи. Продолжаем с риском блокировки...")
    
    async def _scroll_results(self, max_scrolls: int):
        """Скроллит список результатов для подгрузки."""
        prev_count = 0
        same_count = 0
        
        for i in range(max_scrolls):
            try:
                # Находим контейнер со списком
                scroll_container = await self.page.query_selector(".scroll__container")
                if scroll_container:
                    await scroll_container.evaluate("el => el.scrollTop += 1000")
                else:
                    await self.page.evaluate("window.scrollBy(0, 1000)")
                
                await human_delay(1, 2)
                
                # Считаем количество карточек
                cards = await self.page.query_selector_all(".search-snippet-view")
                current_count = len(cards)
                
                if current_count == prev_count:
                    same_count += 1
                    if same_count >= 3:
                        logger.debug("Больше результатов не подгружается")
                        break
                else:
                    same_count = 0
                    prev_count = current_count
                    logger.debug(f"Подгружено карточек: {current_count}")
                
                if MAX_ORGANIZATIONS > 0 and current_count >= MAX_ORGANIZATIONS:
                    break
                    
            except Exception as e:
                logger.debug(f"Ошибка скролла: {e}")
                break
    
    async def _collect_org_links(self, max_results: int) -> List[str]:
        """Собирает ссылки на организации со страницы."""
        links = []
        seen_ids = set()
        
        try:
            # Ищем все ссылки на организации
            org_link_selectors = [
                "a[href*='/maps/org/']",
                "a[href*='/org/']",
                ".search-snippet-view a[href*='/maps/']",
                "[class*='business-snippet'] a"
            ]
            
            for selector in org_link_selectors:
                elements = await self.page.query_selector_all(selector)
                for el in elements:
                    href = await el.get_attribute("href") or ""
                    
                    # Извлекаем чистый URL организации
                    match = re.search(r'(https?://[^"\s]*/maps/org/[^/?\s]+/\d+)', href)
                    if match:
                        org_url = match.group(1).split('?')[0].rstrip('/') + '/'
                        
                        # Извлекаем ID для проверки дублей
                        id_match = re.search(r'/(\d+)/?$', org_url)
                        org_id = id_match.group(1) if id_match else org_url
                        
                        if org_id not in seen_ids and org_url not in links:
                            seen_ids.add(org_id)
                            links.append(org_url)
                            
                            if len(links) >= max_results:
                                return links
            
        except Exception as e:
            logger.error(f"Ошибка сбора ссылок: {e}")
        
        return links[:max_results]
    
    async def _parse_organization_page(self, url: str, rubric: str) -> Optional[Organization]:
        """Парсит страницу организации и возвращает объект Organization."""
        
        try:
            # Переходим на страницу организации
            await self.page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            await human_delay(2, 4)
            
            # Проверяем капчу
            if await self._check_captcha():
                logger.warning("⚠️  Капча на странице организации!")
                await self._wait_for_captcha_solve()
            
            # Получаем HTML страницы
            html = await self.page.content()
            
            org = Organization()
            org.org_url = url
            org.city = self.city
            org.section = rubric  # Основная рубрика
            
            # === ИЗВЛЕЧЕНИЕ ДАННЫХ ===
            
            # Название
            try:
                title_el = await self.page.query_selector("h1.orgpage-header-view__header, h1[class*='header']")
                if title_el:
                    org.name = clean_text(await title_el.inner_text())
            except Exception:
                pass
            
            # Адрес
            try:
                addr_el = await self.page.query_selector(
                    ".orgpage-header-view__address, .business-contacts-view__address, "
                    "[class*='address-line'], a[href*='/house/']"
                )
                if addr_el:
                    org.address = clean_text(await addr_el.inner_text())
            except Exception:
                pass
            
            # Координаты
            lat, lon = extract_coordinates(html)
            org.latitude = lat
            org.longitude = lon
            
            # Телефоны
            phones = []
            mobiles = []
            try:
                # Кликаем "Показать телефон" если есть
                show_phone_btn = self.page.get_by_text("Показать телефон", exact=False).first
                if await show_phone_btn.count() > 0:
                    await show_phone_btn.click(timeout=2000)
                    await human_delay(1, 2)
                
                phone_els = await self.page.query_selector_all(
                    "a[href^='tel:'], [class*='phone-number'], [class*='orgpage-phones']"
                )
                for el in phone_els:
                    phone_text = clean_text(await el.inner_text()) or await el.get_attribute("href") or ""
                    phone_text = phone_text.replace("tel:", "").strip()
                    
                    if phone_text:
                        normalized = parse_phone(phone_text)
                        if normalized:
                            # Определяем мобильный или городской
                            if re.match(r'^\+7[89]\d{9}$', re.sub(r'\D', '', normalized)):
                                mobiles.append(normalized)
                            else:
                                phones.append(normalized)
            except Exception:
                pass
            
            org.phones = ", ".join(unique_list(phones))
            org.mobiles = ", ".join(unique_list(mobiles))
            
            # Сайт
            try:
                site_els = await self.page.query_selector_all("a[href^='http']:not([href*='yandex'])")
                for el in site_els:
                    href = await el.get_attribute("href") or ""
                    if any(domain in href.lower() for domain in ['vk.com', 't.me', 'instagram', 'wa.me']):
                        continue
                    if href and not org.website:
                        org.website = href.split('?')[0]
                        break
            except Exception:
                pass
            
            # Соцсети
            try:
                links = await self.page.query_selector_all("a[href]")
                for el in links:
                    href = await el.get_attribute("href") or ""
                    
                    if 'vk.com' in href.lower():
                        org.vk = href
                    elif 't.me' in href.lower() or 'telegram.org' in href.lower():
                        org.telegram = href
                    elif 'instagram.com' in href.lower():
                        org.instagram = href
                    elif 'max.ru' in href.lower():
                        org.max_social = href
            except Exception:
                pass
            
            # Email
            try:
                email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', html)
                if email_match:
                    org.email = email_match.group(0)
            except Exception:
                pass
            
            # График работы
            try:
                hours_el = await self.page.query_selector(
                    ".business-working-hours-view, [class*='working-hours'], "
                    "[class*='opening-hours'], .orgpage-working-hours-view"
                )
                if hours_el:
                    org.working_hours = clean_text(await hours_el.inner_text())
            except Exception:
                pass
            
            # Рубрики и подразделы
            try:
                rubric_els = await self.page.query_selector_all(
                    "a[href*='/category/'], [class*='category'] a, .business-card-view__breadcrumbs a"
                )
                rubrics_list = []
                subsections_list = []
                
                for i, el in enumerate(rubric_els):
                    text = clean_text(await el.inner_text())
                    if text:
                        if i == 0:
                            subsections_list.append(text)
                        else:
                            rubrics_list.append(text)
                
                org.subsections = ", ".join(unique_list(subsections_list))
                org.rubrics = ", ".join(unique_list(rubrics_list))
            except Exception:
                pass
            
            # Логотип
            try:
                logo_el = await self.page.query_selector(
                    ".orgpage-header-view__logo img, [class*='logo'] img, .business-card-view__logo img"
                )
                if logo_el:
                    org.logo_url = await logo_el.get_attribute("src") or ""
            except Exception:
                pass
            
            # Галерея изображений (до 5)
            try:
                gallery_imgs = await self.page.query_selector_all(
                    ".gallery-view__image img, [class*='photo'] img, .business-gallery-view img"
                )
                images = []
                for el in gallery_imgs[:10]:  # Проверяем первые 10
                    src = await el.get_attribute("src") or ""
                    if src and src not in images:
                        images.append(src)
                        if len(images) >= 5:
                            break
                
                org.gallery_images = ", ".join(images)
            except Exception:
                pass
            
            # Особенности (features) - динамические колонки
            try:
                feature_els = await self.page.query_selector_all(
                    ".card-feature-view, [class*='feature'], .business-feature-view"
                )
                for el in feature_els:
                    feature_text = clean_text(await el.inner_text())
                    if feature_text and ':' in feature_text:
                        key, value = feature_text.split(':', 1)
                        key = clean_text(key)
                        value = clean_text(value)
                        
                        if key and value:
                            # Добавляем в динамические колонки если ещё нет
                            if key not in dynamic_features_columns:
                                dynamic_features_columns.append(key)
                                logger.debug(f"Добавлена новая особенность: {key}")
                            
                            org.features[key] = value
            except Exception:
                pass
            
            # Регион (извлекаем из адреса или используем город)
            org.region = extract_region_from_address(org.address) or self.city
            
            return org
            
        except Exception as e:
            logger.error(f"Ошибка парсинга страницы {url}: {e}")
            return None
    
    def _init_csv(self):
        """Инициализирует CSV файл с заголовками."""
        file_exists = os.path.exists(OUTPUT_CSV)
        
        all_columns = BASE_COLUMNS + dynamic_features_columns
        
        self.csv_file = open(OUTPUT_CSV, 'a', newline='', encoding='utf-8-sig')
        self.csv_writer = csv.DictWriter(self.csv_file, fieldnames=all_columns)
        
        if not file_exists:
            self.csv_writer.writeheader()
            logger.info(f"Создан новый CSV файл: {OUTPUT_CSV}")
    
    def _save_to_csv(self, org: Organization):
        """Сохраняет организацию в CSV."""
        if self.csv_file is None or self.csv_writer is None:
            self._init_csv()
        
        try:
            # Проверяем дубли перед записью
            if self._is_duplicate(org):
                logger.debug(f"Пропуск дубля: {org.name}")
                return
            
            row_data = org.to_dict()
            self.csv_writer.writerow(row_data)
            self.csv_file.flush()  # Гарантируем запись на диск
            
        except Exception as e:
            logger.error(f"Ошибка записи в CSV: {e}")
    
    def _is_duplicate(self, org: Organization) -> bool:
        """Проверяет является ли организация дублем."""
        if not os.path.exists(OUTPUT_CSV):
            return False
        
        try:
            with open(OUTPUT_CSV, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('name') == org.name and row.get('address') == org.address:
                        return True
                    if row.get('org_url') == org.org_url:
                        return True
        except Exception:
            pass
        
        return False
    
    def close_csv(self):
        """Закрывает CSV файл."""
        if self.csv_file:
            self.csv_file.close()
            self.csv_file = None


def unique_list(items: List[str]) -> List[str]:
    """Возвращает список уникальных значений с сохранением порядка."""
    seen = set()
    result = []
    for item in items:
        cleaned = clean_text(item).lower()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(item)
    return result


def extract_region_from_address(address: str) -> str:
    """Извлекает регион из адреса."""
    if not address:
        return ""
    
    # Простой эвристический поиск региона
    regions = {
        'москва': 'Москва',
        'санкт-петербург': 'Санкт-Петербург',
        'спб': 'Санкт-Петербург',
        'казань': 'Республика Татарстан',
        'екатеринбург': 'Свердловская область',
        'новосибирск': 'Новосибирская область',
        'нижний новгород': 'Нижегородская область',
        'самара': 'Самарская область',
        'омск': 'Омская область',
        'ufa': 'Республика Башкортостан',
    }
    
    addr_lower = address.lower()
    for key, region in regions.items():
        if key in addr_lower:
            return region
    
    # Поиск области/края/республики
    match = re.search(r'(область|край|республика)\s+\w+', address, re.IGNORECASE)
    if match:
        return match.group(0)
    
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# ГЛАВНЫЙ КЛАСС ПАРСЕРА
# ─────────────────────────────────────────────────────────────────────────────

class YandexMapsParser:
    """Основной класс парсера Яндекс.Карт."""
    
    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None
    
    async def start(self):
        """Запускает браузер и инициализирует парсер."""
        setup_logger()
        
        logger.info("🚀 Запуск Yandex Maps Parser с обходом лимитов")
        logger.info(f"Город: {CITY}")
        logger.info(f"Макс. организаций: {MAX_ORGANIZATIONS if MAX_ORGANIZATIONS > 0 else 'без ограничений'}")
        logger.info(f"Выходной файл: {OUTPUT_CSV}")
        
        from playwright.async_api import async_playwright
        
        playwright = await async_playwright().start()
        
        # Запускаем Chromium с настройками stealth
        self.browser = await playwright.chromium.launch(
            headless=not DEBUG_MODE,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-dev-shm-usage",
            ]
        )
        
        self.context = await self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        )
        
        self.page = await self.context.new_page()
        
        # Применяем stealth для обхода детекции бота
        await stealth_async(self.page)
        
        logger.success("✅ Браузер запущен и настроен")
    
    async def run(self):
        """Запускает полный цикл парсинга."""
        await self.start()
        
        try:
            # ЭТАП 1: Сбор рубрик
            rubric_parser = RubricParser(self.page)
            rubrics = await rubric_parser.collect_rubrics(CITY)
            
            if not rubrics:
                logger.error("❌ Не удалось собрать рубрики. Завершение работы.")
                return
            
            logger.info(f"📋 Всего рубрик для обработки: {len(rubrics)}")
            
            # ЭТАП 2: Поиск по каждой рубрике
            org_parser = OrganizationParser(self.page, CITY)
            
            total_orgs = 0
            for idx, rubric in enumerate(rubrics, 1):
                if MAX_ORGANIZATIONS > 0 and total_orgs >= MAX_ORGANIZATIONS:
                    logger.info("🎯 Достигнут лимит организаций")
                    break
                
                logger.info(f"\n{'='*60}")
                logger.info(f"Рубрика {idx}/{len(rubrics)}: {rubric}")
                logger.info(f"{'='*60}\n")
                
                orgs = await org_parser.search_by_rubric(rubric)
                total_orgs += len(orgs)
                
                logger.success(f"✅ По рубрике '{rubric}' собрано: {len(orgs)} организаций")
                logger.info(f"📊 ВСЕГО собрано: {total_orgs}")
                
                # Пауза между рубриками
                if idx < len(rubrics):
                    await human_delay(5, 10)
            
            org_parser.close_csv()
            
            logger.success(f"\n{'='*60}")
            logger.success(f"🎉 ПАРСИНГ ЗАВЕРШЕН!")
            logger.success(f"Всего собрано организаций: {total_orgs}")
            logger.success(f"Результаты сохранены в: {OUTPUT_CSV}")
            logger.success(f"{'='*60}")
            
        except Exception as e:
            logger.error(f"Критическая ошибка: {e}")
            raise
        finally:
            await self.stop()
    
    async def stop(self):
        """Останавливает браузер."""
        if self.browser:
            await self.browser.close()
            logger.info("👋 Браузер закрыт")


# ─────────────────────────────────────────────────────────────────────────────
# ТОЧКА ВХОДА
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    """Главная функция."""
    parser = YandexMapsParser()
    await parser.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("\n⚠️  Парсинг прерван пользователем")
    except Exception as e:
        logger.error(f"Фатальная ошибка: {e}")
        sys.exit(1)
