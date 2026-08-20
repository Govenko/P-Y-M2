#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yandex Maps Ultimate Parser - Надёжный парсер с двухэтапным обходом лимитов.

АРХИТЕКТУРА И СТРАТЕГИЯ ОБХОДА ЛИМИТОВ:
========================================

ПРОБЛЕМА:
Яндекс.Карты ограничивают выдачу ~500 организациями на один поисковый запрос.
При поиске "кофейни Москва" вы получите максимум 500 результатов, даже если 
в городе их несколько тысяч.

РЕШЕНИЕ - ДВУХЭТАПНЫЙ ПОДХОД:

┌─────────────────────────────────────────────────────────────────┐
│                    ЭТАП 1: СБОР РУБРИК                          │
├─────────────────────────────────────────────────────────────────┤
│ 1. Переходим на Яндекс.Карты по выбранному городу              │
│ 2. Открываем рубрикатор (категории организаций)                │
│ 3. Последовательно раскрываем все категории и подкатегории     │
│ 4. Извлекаем полный список рубрик города (50-500+ штук)        │
│ 5. Сохраняем список для обработки                              │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│              ЭТАП 2: ПОИСК ПО КАЖДОЙ РУБРИКЕ                    │
├─────────────────────────────────────────────────────────────────┤
│ 1. Берём первую рубрику из списка (например "Кофейни")         │
│ 2. Выполняем поиск по этой рубрике в городе                   │
│ 3. Получаем до 500 результатов ТОЛЬКО по этой категории        │
│ 4. Парсим каждую карточку организации                          │
│ 5. Сохраняем данные немедленно в CSV                           │
│ 6. Повторяем для следующей рубрики                             │
│                                                                 │
│ РЕЗУЛЬТАТ: 100 рубрик × 500 org = до 50,000 организаций!       │
└─────────────────────────────────────────────────────────────────┘

КЛЮЧЕВЫЕ ОСОБЕННОСТИ:
• playwright-stealth для маскировки под обычного пользователя
• Рандомизированные человеческие задержки (2-6 сек)
• Обработка капчи через ручное вмешательство (браузер открыт)
• Построчная запись в CSV (защита от потери данных)
• Пропуск дублей по URL/названию
• Динамические колонки для особенностей организаций
• Логирование через loguru с сохранением в файл

Автор: Senior Python Developer (Web Scraping Expert)
Версия: 2.0
"""

import asyncio
import csv
import os
import random
import re
import sys
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Set, Dict, List, Any, Tuple

from dotenv import load_dotenv
from loguru import logger
from playwright.async_api import async_playwright, Page, BrowserContext, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth

# Создаем экземпляр Stealth для обхода детекции бота
stealth_instance = Stealth()

# ─────────────────────────────────────────────────────────────────────────────
# КОНФИГУРАЦИЯ (можно переопределить через .env)
# ─────────────────────────────────────────────────────────────────────────────

load_dotenv()

# Основные настройки
CITY = os.getenv("CITY", "Москва")
MAX_ORGANIZATIONS = int(os.getenv("MAX_ORGANIZATIONS", "0"))  # 0 = без ограничений
OUTPUT_CSV = os.getenv("OUTPUT_CSV", "yandex_maps_result.csv")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "results")

# Настройки задержек (человеческое поведение)
MIN_DELAY = float(os.getenv("MIN_DELAY", "2.0"))
MAX_DELAY = float(os.getenv("MAX_DELAY", "6.0"))
SCROLL_DELAY = float(os.getenv("SCROLL_DELAY", "1.5"))

# Режимы работы
DEBUG_MODE = os.getenv("DEBUG_MODE", "True").lower() == "true"  # Показывать браузер
HEADLESS = not DEBUG_MODE

# Таймауты
PAGE_TIMEOUT = int(os.getenv("PAGE_TIMEOUT", "60000"))
ELEMENT_TIMEOUT = int(os.getenv("ELEMENT_TIMEOUT", "10000"))

# Лимиты скролла
MAX_SCROLLS_RUBRICS = int(os.getenv("MAX_SCROLLS_RUBRICS", "30"))
MAX_SCROLLS_SEARCH = int(os.getenv("MAX_SCROLLS_SEARCH", "50"))
STAGNANT_SCROLL_ROUNDS = int(os.getenv("STAGNANT_SCROLL_ROUNDS", "3"))

# Базовые колонки CSV (согласно ТЗ)
BASE_COLUMNS = [
    "name",           # 1. Название организации
    "region",         # 2. Регион
    "city",           # 3. Населенный пункт
    "address",        # 4. Адрес
    "section",        # 5. Раздел
    "subsections",    # 6. Подразделы (через запятую)
    "rubrics",        # 7. Рубрики (через запятую)
    "phones",         # 8. Телефоны (через запятую)
    "mobiles",        # 9. Мобильные номера (через запятую)
    "email",          # 10. E-mail
    "website",        # 11. Сайт
    "vk",             # 12. Ссылка на ВК
    "telegram",       # 13. Ссылка на Telegram
    "instagram",      # 14. Ссылка на Instagram
    "max",            # 15. Ссылка на социальную сеть MAX
    "latitude",       # 16. Широта
    "longitude",      # 17. Долгота
    "working_hours",  # 18. График работы текстом
    "logo_url",       # 19. Ссылка на логотип
    "gallery_images", # 20. До 5 изображений из галереи (через запятую)
    # 21+. Динамические колонки для особенностей (features)
]

# Глобальное хранилище динамических колонок (особенности)
dynamic_features_columns: List[str] = []

# Множество уже обработанных URL для предотвращения дублей
processed_org_urls: Set[str] = set()

# Ссылка на Яндекс.Карты
YANDEX_MAPS_BASE = "https://yandex.ru/maps"


# ─────────────────────────────────────────────────────────────────────────────
# МОДЕЛИ ДАННЫХ
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Organization:
    """
    Модель организации со всеми полями согласно ТЗ.
    
    Атрибуты:
        name: Название организации
        region: Регион (область, край, республика)
        city: Город
        address: Полный адрес
        section: Основной раздел
        subsections: Подразделы (через запятую)
        rubrics: Все рубрики (через запятую)
        phones: Городские телефоны
        mobiles: Мобильные номера
        email: Электронная почта
        website: Официальный сайт
        vk: Ссылка на ВКонтакте
        telegram: Ссылка на Telegram
        instagram: Ссылка на Instagram
        max_social: Ссылка на MAX
        latitude: Широта
        longitude: Долгота
        working_hours: График работы
        logo_url: URL логотипа
        gallery_images: URLs изображений галереи
        features: Словарь особенностей (ключ-значение)
        org_url: URL страницы организации на Яндекс.Картах
    """
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
        """
        Конвертирует организацию в словарь для записи в CSV.
        
        Returns:
            Словарь со всеми полями включая динамические особенности.
        """
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
        """
        Уникальный ключ организации для проверки дублей.
        
        Returns:
            URL организации или комбинация названия и адреса.
        """
        if self.org_url:
            return self.org_url
        return f"{self.name.lower()}|{self.address.lower()}"
    
    @property
    def total_fields_count(self) -> int:
        """Возвращает общее количество заполненных полей."""
        return len([v for v in self.to_dict().values() if v])


# ─────────────────────────────────────────────────────────────────────────────
# УТИЛИТЫ И ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────────────────────────────────────────

def setup_logger():
    """
    Настраивает логгер loguru для красивого и информативного вывода.
    
    Логи выводятся:
    1. В консоль с цветным форматированием
    2. В файл parser.log с ротацией по размеру (10 MB)
    3. С сохранением последних 7 дней логов
    """
    logger.remove()  # Удаляем стандартный обработчик
    
    # Консольный вывод с цветами
    logger.add(
        sys.stdout,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>",
        level="INFO",
        colorize=True
    )
    
    # Файловый вывод с ротацией
    logger.add(
        "parser.log",
        rotation="10 MB",
        retention="7 days",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}",
        level="DEBUG",
        encoding="utf-8"
    )


async def human_delay(min_sec: float = None, max_sec: float = None):
    """
    Реализует случайную задержку для имитации человеческого поведения.
    
    Args:
        min_sec: Минимальная задержка в секундах
        max_sec: Максимальная задержка в секундах
    
    Пример:
        await human_delay(2, 5)  # Задержка от 2 до 5 секунд
    """
    if min_sec is None:
        min_sec = MIN_DELAY
    if max_sec is None:
        max_sec = MAX_DELAY
    
    delay = random.uniform(min_sec, max_sec)
    logger.debug(f"Задержка {delay:.2f} сек")
    await asyncio.sleep(delay)


def clean_text(text: Optional[str]) -> str:
    """
    Очищает текст от лишних пробелов и символов.
    
    Args:
        text: Исходный текст
        
    Returns:
        Очищенный текст или пустая строка
    """
    if not text:
        return ""
    # Заменяем множественные пробелы на один
    cleaned = re.sub(r'\s+', ' ', text.strip())
    # Удаляем невидимые символы
    cleaned = re.sub(r'[\u200b\u200c\u200d\ufeff]', '', cleaned)
    return cleaned


def extract_coordinates(html: str) -> Tuple[str, str]:
    """
    Извлекает координаты (широту и долготу) из HTML страницы.
    
    Поддерживаемые форматы:
    - "coordinates": [lon, lat]
    - point: [lon, lat]
    - ll=lon,lat в URL
    
    Args:
        html: HTML содержимое страницы
        
    Returns:
        Кортеж (latitude, longitude) или ("", "") если не найдено
    """
    lat, lon = "", ""
    
    # Паттерн 1: "coordinates": [долгота, широта]
    match = re.search(r'"coordinates"\s*:\s*\[([\d\.\-]+),\s*([\d\.\-]+)\]', html)
    if match:
        lon, lat = match.group(1), match.group(2)
    
    # Паттерн 2: point: [долгота, широта]
    if not lat:
        match = re.search(r'point\s*:\s*\[([\d\.\-]+),\s*([\d\.\-]+)\]', html)
        if match:
            lon, lat = match.group(1), match.group(2)
    
    # Паттерн 3: ll=долгота,широта в URL
    if not lat:
        match = re.search(r'll=([\d\.\-]+),([\d\.\-]+)', html)
        if match:
            lon, lat = match.group(1), match.group(2)
    
    # Паттерн 4: geoObjectCoordinates
    if not lat:
        match = re.search(r'geoObjectCoordinates"\s*:\s*\[([\d\.\-]+),\s*([\d\.\-]+)\]', html)
        if match:
            lon, lat = match.group(1), match.group(2)
    
    return lat, lon


def parse_phone(phone_str: str) -> Optional[str]:
    """
    Нормализует телефонный номер к единому формату.
    
    Поддерживаемые форматы:
    - +7 (XXX) XXX-XX-XX
    - 8 (XXX) XXX-XX-XX
    - +7XXXXXXXXXX
    - 8XXXXXXXXXX
    
    Args:
        phone_str: Исходная строка с телефоном
        
    Returns:
        Нормализованный номер или None если не удалось распознать
    """
    if not phone_str:
        return None
    
    # Извлекаем только цифры
    digits = re.sub(r'\D', '', phone_str)
    
    # Обрабатываем российские номера
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    elif len(digits) == 10:
        digits = '7' + digits
    elif len(digits) == 12 and digits.startswith('+7'):
        digits = digits[1:]  # Убираем +
    
    # Форматируем: +7 (XXX) XXX-XX-XX
    if len(digits) == 11 and digits.startswith('7'):
        return f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    
    # Возвращаем как есть если не подошло под российский формат
    return clean_text(phone_str) if len(digits) >= 10 else None


def is_mobile_phone(phone: str) -> bool:
    """
    Определяет является ли номер мобильным.
    
    Args:
        phone: Нормализованный номер телефона
        
    Returns:
        True если мобильный, False если городской
    """
    digits = re.sub(r'\D', '', phone)
    # Российские мобильные начинаются с +79 или 89
    if len(digits) == 11 and digits.startswith('7') and digits[1] == '9':
        return True
    return False


def unique_list(items: List[str], limit: int = 20) -> List[str]:
    """
    Возвращает список уникальных значений с сохранением порядка.
    
    Args:
        items: Исходный список
        limit: Максимальное количество элементов
        
    Returns:
        Список уникальных элементов
    """
    seen = set()
    result = []
    for item in items:
        cleaned = clean_text(item).lower()
        if cleaned and cleaned not in seen and len(result) < limit:
            seen.add(cleaned)
            result.append(item)
    return result


def extract_region_from_address(address: str) -> str:
    """
    Извлекает регион из адреса организации.
    
    Args:
        address: Полный адрес
        
    Returns:
        Название региона или пустая строка
    """
    if not address:
        return ""
    
    addr_lower = address.lower()
    
    # Словарь регионов для крупных городов
    regions_map = {
        'москва': 'Москва',
        'санкт-петербург': 'Санкт-Петербург',
        'спб': 'Санкт-Петербург',
        'казань': 'Республика Татарстан',
        'екатеринбург': 'Свердловская область',
        'новосибирск': 'Новосибирская область',
        'нижний новгород': 'Нижегородская область',
        'самара': 'Самарская область',
        'омск': 'Омская область',
        'уфа': 'Республика Башкортостан',
        'челябинск': 'Челябинская область',
        'ростов-на-дону': 'Ростовская область',
        'краснодар': 'Краснодарский край',
        'воронеж': 'Воронежская область',
        'пермь': 'Пермский край',
        'тюмень': 'Тюменская область',
        'красноярск': 'Красноярский край',
        'владивосток': 'Приморский край',
    }
    
    # Поиск по словарю
    for city, region in regions_map.items():
        if city in addr_lower:
            return region
    
    # Поиск административных единиц
    admin_patterns = [
        r'(область)\s+(\w+)',
        r'(край)\s+(\w+)',
        r'(республика)\s+(\w+)',
        r'(автономная\s+область)',
        r'(автономный\s+округ)',
    ]
    
    for pattern in admin_patterns:
        match = re.search(pattern, address, re.IGNORECASE)
        if match:
            return match.group(0).strip()
    
    return ""


def safe_get_selector(page: Page, selectors: List[str], timeout: int = None) -> Any:
    """
    Безопасно получает элемент по первому подходящему селектору.
    
    Args:
        page: Страница Playwright
        selectors: Список CSS селекторов
        timeout: Таймаут в мс
        
    Returns:
        ElementHandle или None
    """
    if timeout is None:
        timeout = ELEMENT_TIMEOUT
    
    for selector in selectors:
        try:
            element = page.locator(selector).first
            if element.count() > 0:
                return element
        except Exception:
            continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# ПАРСЕР РУБРИК (ЭТАП 1)
# ─────────────────────────────────────────────────────────────────────────────

class RubricParser:
    """
    Парсер для сбора всех доступных рубрик города.
    
    Это первый этап двухэтапной стратегии обхода лимитов.
    Собирает категории и подкатегории организаций для последующего
    независимого поиска по каждой из них.
    """
    
    def __init__(self, page: Page):
        """
        Инициализирует парсер рубрик.
        
        Args:
            page: Страница Playwright
        """
        self.page = page
        self.rubrics: Set[str] = set()
    
    async def collect_all_rubrics(self, city: str) -> List[str]:
        """
        Собирает все доступные рубрики для указанного города.
        
        Алгоритм:
        1. Переход на страницу города
        2. Принятие cookies
        3. Поиск и открытие рубрикатора
        4. Последовательный сбор всех категорий
        5. Возврат уникального списка
        
        Args:
            city: Название города
            
        Returns:
            Список названий рубрик
        """
        logger.info(f"📋 ЭТАП 1: Сбор рубрик для города '{city}'")
        self.rubrics.clear()
        
        # Кодируем город для URL
        city_encoded = city.replace(' ', '+')
        url = f"{YANDEX_MAPS_BASE}/?text={city_encoded}"
        
        try:
            # Переход на страницу
            logger.debug(f"Переход на страницу: {url}")
            await self.page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            await human_delay(3, 5)
            
            # Принятие cookies
            await self._accept_cookies()
            
            # Ожидание загрузки интерфейса
            await human_delay(2, 4)
            
            # Сбор рубрик основным способом
            await self._collect_rubrics_from_categories()
            
            # Если мало рубрик, пробуем альтернативные способы
            if len(self.rubrics) < 10:
                logger.warning("Мало рубрик найдено, пробуем альтернативный сбор...")
                await self._collect_rubrics_alternative(city)
            
            # Добавляем популярные рубрики если всё ещё мало
            if len(self.rubrics) < 5:
                logger.warning("Используем fallback список популярных рубрик")
                self._add_fallback_rubrics()
            
            result = list(self.rubrics)
            logger.success(f"✅ Найдено {len(result)} рубрик для города '{city}'")
            return result
            
        except Exception as e:
            logger.error(f"❌ Ошибка при сборе рубрик: {e}")
            self._add_fallback_rubrics()
            return list(self.rubrics)
    
    async def _accept_cookies(self):
        """Принимает соглашения о cookies."""
        try:
            cookie_texts = ["Принять все", "Allow all", "OK", "Хорошо", "Согласен"]
            for text in cookie_texts:
                button = self.page.get_by_text(text, exact=False).first
                if await button.count() > 0:
                    await button.click(timeout=2000)
                    logger.debug(f"Приняты cookies: {text}")
                    await human_delay(1, 2)
                    break
        except Exception as e:
            logger.debug(f"Cookies уже приняты или не требуются: {e}")
    
    async def _collect_rubrics_from_categories(self):
        """Собирает рубрики из основного рубрикатора."""
        try:
            # Ищем кнопку/categories или фильтр
            category_selectors = [
                ".categories-view__category",
                "[class*='category'] a",
                ".rubric-item",
                "[data-testid='category-item']",
                ".filter-categories a",
            ]
            
            for selector in category_selectors:
                elements = self.page.locator(selector)
                count = await elements.count()
                
                if count > 0:
                    logger.debug(f"Найдено {count} элементов по селектору {selector}")
                    
                    for i in range(count):
                        try:
                            el = elements.nth(i)
                            text = await el.inner_text()
                            text = clean_text(text)
                            
                            if text and len(text) > 2 and len(text) < 100:
                                self.rubrics.add(text)
                        except Exception:
                            continue
                    
                    # Скроллим для подгрузки
                    await self._scroll_container(elements, MAX_SCROLLS_RUBRICS)
                    break
            
        except Exception as e:
            logger.debug(f"Ошибка сбора из категорий: {e}")
    
    async def _scroll_container(self, elements, max_scrolls: int):
        """Скроллит контейнер для подгрузки элементов."""
        try:
            prev_count = 0
            same_rounds = 0
            
            for scroll_num in range(max_scrolls):
                # Считаем текущее количество
                current_count = await elements.count()
                
                if current_count == prev_count:
                    same_rounds += 1
                    if same_rounds >= STAGNANT_SCROLL_ROUNDS:
                        break
                else:
                    same_rounds = 0
                    prev_count = current_count
                
                # Скролл вниз
                await self.page.evaluate("window.scrollBy(0, 800)")
                await human_delay(SCROLL_DELAY - 0.5, SCROLL_DELAY + 0.5)
                
        except Exception as e:
            logger.debug(f"Ошибка скролла: {e}")
    
    async def _collect_rubrics_alternative(self, city: str):
        """Альтернативный способ сбора через поиск популярных запросов."""
        popular_queries = [
            "рестораны", "кафе", "магазины", "аптеки", "больницы",
            "школы", "детские сады", "банки", "отели", "салоны красоты",
            "парикмахерские", "автосервисы", "fitness", "клубы", "бары"
        ]
        
        for query in popular_queries:
            try:
                search_url = f"{YANDEX_MAPS_BASE}/?text={query}+{city.replace(' ', '+')}"
                await self.page.goto(search_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT // 2)
                await human_delay(2, 3)
                
                # Пробуем найти категорию в результатах
                category_els = self.page.locator("[class*='category']")
                count = await category_els.count()
                
                for i in range(min(count, 5)):
                    try:
                        text = await category_els.nth(i).inner_text()
                        text = clean_text(text)
                        if text and len(text) > 2:
                            self.rubrics.add(text)
                    except Exception:
                        continue
                        
            except Exception:
                continue
    
    def _add_fallback_rubrics(self):
        """Добавляет fallback список популярных рубрик."""
        fallback = [
            "Рестораны", "Кафе", "Столовые", "Бары", "Пиццерии",
            "Магазины одежды", "Продуктовые магазины", "Аптеки",
            "Банки", "Отели", "Хостелы", "Салоны красоты",
            "Парикмахерские", "Автосервисы", "Автомойки",
            "Фитнес-клубы", "Бассейны", "Кинотеатры",
            "Театры", "Музеи", "Парки", "Торговые центры",
            "Школы", "Детские сады", "Университеты",
            "Больницы", "Поликлиники", "Ветклиники",
            "Нотариусы", "Юридические услуги", "Бухгалтерские услуги",
            "Строительные компании", "Ремонт квартир",
            "Такси", "Каршеринг", "Прокат автомобилей"
        ]
        for rubric in fallback:
            self.rubrics.add(rubric)


# ─────────────────────────────────────────────────────────────────────────────
# ПАРСЕР ОРГАНИЗАЦИЙ (ЭТАП 2)
# ─────────────────────────────────────────────────────────────────────────────

class OrganizationParser:
    """
    Парсер для сбора данных об организациях по рубрикам.
    
    Это второй этап двухэтапной стратегии. Для каждой рубрики:
    1. Выполняет поиск в городе
    2. Скроллит результаты для максимизации выдачи
    3. Собирает ссылки на карточки организаций
    4. Парсит детальную информацию из каждой карточки
    5. Немедленно сохраняет в CSV
    """
    
    def __init__(self, page: Page, city: str):
        """
        Инициализирует парсер организаций.
        
        Args:
            page: Страница Playwright
            city: Город для поиска
        """
        self.page = page
        self.city = city
        self.csv_file = None
        self.csv_writer = None
        self.stats = {
            "total": 0,
            "parsed": 0,
            "skipped_duplicates": 0,
            "errors": 0
        }
    
    async def search_by_rubric(self, rubric: str) -> int:
        """
        Выполняет поиск по конкретной рубрике и парсит результаты.
        
        Args:
            rubric: Название рубрики для поиска
            
        Returns:
            Количество успешно спарсенных организаций
        """
        logger.info(f"🔍 Поиск по рубрике: '{rubric}'")
        
        # Формируем поисковый запрос
        query = f"{rubric} {self.city}".strip()
        url = f"{YANDEX_MAPS_BASE}/?text={query.replace(' ', '+')}"
        
        try:
            # Переход на страницу поиска
            await self.page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            await human_delay(3, 5)
            
            # Принятие cookies
            await self._accept_cookies()
            await human_delay(2, 3)
            
            # Проверка на капчу
            if await self._check_captcha():
                logger.warning("⚠️  Обнаружена капча! Ожидание решения...")
                await self._wait_for_manual_captcha_solve()
            
            # Скроллинг для подгрузки результатов
            await self._scroll_search_results()
            
            # Сбор ссылок на организации
            org_links = await self._collect_organization_links(MAX_ORGANIZATIONS if MAX_ORGANIZATIONS > 0 else 500)
            logger.info(f"📁 Найдено {len(org_links)} ссылок на организации")
            
            # Инициализация CSV если нужно
            self._init_csv()
            
            # Парсинг каждой организации
            parsed_count = 0
            for idx, org_url in enumerate(org_links, 1):
                # Проверка на достижение лимита
                if MAX_ORGANIZATIONS > 0 and self.stats["parsed"] >= MAX_ORGANIZATIONS:
                    logger.info("🎯 Достигнут максимальный лимит организаций")
                    break
                
                # Пропуск уже обработанных
                if org_url in processed_org_urls:
                    logger.debug(f"Пропуск дубля: {org_url}")
                    self.stats["skipped_duplicates"] += 1
                    continue
                
                processed_org_urls.add(org_url)
                self.stats["total"] += 1
                
                logger.debug(f"[{idx}/{len(org_links)}] Обработка: {org_url}")
                
                # Парсинг организации
                org = await self._parse_organization_page(org_url, rubric)
                
                if org and org.name:
                    self._save_to_csv(org)
                    parsed_count += 1
                    self.stats["parsed"] += 1
                    logger.success(f"  ✅ {org.name} | {org.phones or 'нет телефона'}")
                else:
                    self.stats["errors"] += 1
                    logger.warning(f"  ⚠️  Не удалось спарсить: {org_url}")
                
                # Человеческая задержка между организациями
                await human_delay(3, 6)
            
            return parsed_count
            
        except Exception as e:
            logger.error(f"❌ Ошибка при поиске по рубрике '{rubric}': {e}")
            return 0
    
    async def _accept_cookies(self):
        """Принимает cookies."""
        try:
            for text in ["Принять все", "Allow all", "OK"]:
                button = self.page.get_by_text(text, exact=False).first
                if await button.count() > 0:
                    await button.click(timeout=2000)
                    await human_delay(1, 2)
                    break
        except Exception:
            pass
    
    async def _check_captcha(self) -> bool:
        """
        Проверяет наличие капчи на странице.
        
        Returns:
            True если капча обнаружена
        """
        captcha_indicators = [
            lambda: "captcha" in self.page.url.lower(),
            lambda: "showcaptcha" in self.page.url.lower(),
            lambda: self.page.locator("#smart-captcha").count() > 0,
            lambda: self.page.locator("[class*='captcha']").count() > 0,
            lambda: self.page.locator("iframe[src*='captcha']").count() > 0,
        ]
        
        for indicator in captcha_indicators:
            try:
                if indicator():
                    return True
            except Exception:
                continue
        
        return False
    
    async def _wait_for_manual_captcha_solve(self, timeout_sec: int = 120):
        """
        Ждет пока пользователь вручную решит капчу.
        
        Args:
            timeout_sec: Максимальное время ожидания в секундах
        """
        logger.warning("=" * 60)
        logger.warning("🖐️  ТРЕБУЕТСЯ ВАШЕ ВМЕШАТЕЛЬСТВО!")
        logger.warning("Решите капчу в открытом окне браузера.")
        logger.warning(f"Время на решение: {timeout_sec} секунд")
        logger.warning("=" * 60)
        
        start_time = asyncio.get_event_loop().time()
        
        while True:
            await asyncio.sleep(3)
            
            # Проверяем решена ли капча
            if not await self._check_captcha():
                logger.success("✅ Капча решена! Продолжаем работу...")
                await human_delay(2, 3)
                break
            
            # Проверка таймаута
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= timeout_sec:
                logger.error("⏰ Таймаут ожидания капчи!")
                break
    
    async def _scroll_search_results(self):
        """Скроллит результаты поиска для подгрузки максимального количества."""
        logger.debug("Начинаю скроллинг результатов...")
        
        prev_count = 0
        same_rounds = 0
        
        for scroll_num in range(MAX_SCROLLS_SEARCH):
            try:
                # Считаем карточки
                cards = self.page.locator(".search-snippet-view, [class*='business-snippet']")
                current_count = await cards.count()
                
                if current_count == prev_count:
                    same_rounds += 1
                    if same_rounds >= STAGNANT_SCROLL_ROUNDS:
                        logger.debug(f"Больше результатов не подгружается после {scroll_num} скроллов")
                        break
                else:
                    same_rounds = 0
                    prev_count = current_count
                    logger.debug(f"Скролл {scroll_num}: найдено {current_count} карточек")
                
                # Скролл вниз
                await self.page.evaluate("window.scrollBy(0, 1000)")
                await human_delay(SCROLL_DELAY - 0.3, SCROLL_DELAY + 0.3)
                
                # Проверка лимита
                if MAX_ORGANIZATIONS > 0 and current_count >= MAX_ORGANIZATIONS:
                    break
                    
            except Exception as e:
                logger.debug(f"Ошибка скролла: {e}")
                break
    
    async def _collect_organization_links(self, max_results: int) -> List[str]:
        """
        Собирает ссылки на карточки организаций со страницы поиска.
        
        Args:
            max_results: Максимальное количество ссылок
            
        Returns:
            Список URL организаций
        """
        links = []
        seen_ids = set()
        
        link_selectors = [
            "a[href*='/maps/org/']",
            "a[href*='/org/'][href*='/maps']",
            ".search-snippet-view a[href*='/maps']",
            "[class*='business-snippet'] a[href*='/']",
        ]
        
        try:
            for selector in link_selectors:
                elements = self.page.locator(selector)
                count = await elements.count()
                
                for i in range(count):
                    if len(links) >= max_results:
                        break
                    
                    try:
                        href = await elements.nth(i).get_attribute("href") or ""
                        
                        # Извлекаем чистый URL организации
                        match = re.search(r'(https?://[^"\s]*/maps/org/[^/?\s]+/\d+)', href)
                        if match:
                            org_url = match.group(1).split('?')[0].rstrip('/') + '/'
                            
                            # Проверка на дубли по ID
                            id_match = re.search(r'/(\d+)/?$', org_url)
                            org_id = id_match.group(1) if id_match else org_url
                            
                            if org_id not in seen_ids and org_url not in links:
                                seen_ids.add(org_id)
                                links.append(org_url)
                                
                    except Exception:
                        continue
                
                if len(links) >= max_results:
                    break
                    
        except Exception as e:
            logger.error(f"Ошибка сбора ссылок: {e}")
        
        return links[:max_results]
    
    async def _parse_organization_page(self, url: str, rubric: str) -> Optional[Organization]:
        """
        Парсит страницу организации и извлекает все данные.
        
        Args:
            url: URL страницы организации
            rubric: Рубрика поиска
            
        Returns:
            Объект Organization или None при ошибке
        """
        try:
            # Переход на страницу
            await self.page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            await human_delay(2, 4)
            
            # Проверка капчи
            if await self._check_captcha():
                logger.warning(f"⚠️  Капча на странице организации!")
                await self._wait_for_manual_captcha_solve(timeout_sec=60)
            
            # Получаем HTML для анализа
            html = await self.page.content()
            
            org = Organization()
            org.org_url = url
            org.city = self.city
            org.section = rubric
            
            # === ИЗВЛЕЧЕНИЕ НАЗВАНИЯ ===
            try:
                title_selectors = [
                    "h1.orgpage-header-view__header",
                    "h1[class*='header']",
                    "h1.business-card-header__title",
                    "[class*='orgpage-header'] h1",
                ]
                title_el = safe_get_selector(self.page, title_selectors)
                if title_el:
                    org.name = clean_text(await title_el.inner_text())
            except Exception as e:
                logger.debug(f"Ошибка извлечения названия: {e}")
            
            # === ИЗВЛЕЧЕНИЕ АДРЕСА ===
            try:
                addr_selectors = [
                    ".orgpage-header-view__address",
                    ".business-contacts-view__address",
                    "[class*='address-line']",
                    "a[href*='/house/']",
                    "[class*='geo'] a[href*='/place/']",
                ]
                addr_el = safe_get_selector(self.page, addr_selectors)
                if addr_el:
                    org.address = clean_text(await addr_el.inner_text())
            except Exception as e:
                logger.debug(f"Ошибка извлечения адреса: {e}")
            
            # === ИЗВЛЕЧЕНИЕ КООРДИНАТ ===
            lat, lon = extract_coordinates(html)
            org.latitude = lat
            org.longitude = lon
            
            # === ИЗВЛЕЧЕНИЕ ТЕЛЕФОНОВ ===
            phones_list = []
            mobiles_list = []
            
            try:
                # Кликаем "Показать телефон" если есть
                show_phone_btn = self.page.get_by_text("Показать телефон", exact=False).first
                if await show_phone_btn.count() > 0:
                    try:
                        await show_phone_btn.click(timeout=2000)
                        await human_delay(1, 2)
                        logger.debug("Нажата кнопка 'Показать телефон'")
                    except Exception:
                        pass
                
                # Ищем телефоны
                phone_selectors = [
                    "a[href^='tel:']",
                    "[class*='phone-number']",
                    "[class*='orgpage-phones']",
                    "[class*='business-phone']",
                ]
                
                for selector in phone_selectors:
                    phone_els = self.page.locator(selector)
                    count = await phone_els.count()
                    
                    for i in range(count):
                        try:
                            el = phone_els.nth(i)
                            phone_text = clean_text(await el.inner_text())
                            
                            if not phone_text:
                                phone_text = await el.get_attribute("href") or ""
                            
                            phone_text = phone_text.replace("tel:", "").strip()
                            
                            if phone_text:
                                normalized = parse_phone(phone_text)
                                if normalized:
                                    if is_mobile_phone(normalized):
                                        mobiles_list.append(normalized)
                                    else:
                                        phones_list.append(normalized)
                        except Exception:
                            continue
                    
                    if phones_list or mobiles_list:
                        break
                        
            except Exception as e:
                logger.debug(f"Ошибка извлечения телефонов: {e}")
            
            org.phones = ", ".join(unique_list(phones_list))
            org.mobiles = ", ".join(unique_list(mobiles_list))
            
            # === ИЗВЛЕЧЕНИЕ САЙТА ===
            try:
                site_selectors = [
                    "a[href^='http']:not([href*='yandex']):not([href*='vk.com']):not([href*='t.me'])",
                    "[class*='website'] a",
                    "[class*='site'] a",
                ]
                
                for selector in site_selectors:
                    site_els = self.page.locator(selector)
                    count = await site_els.count()
                    
                    for i in range(count):
                        try:
                            href = await site_els.nth(i).get_attribute("href") or ""
                            if href and not org.website:
                                # Исключаем соцсети
                                if not any(domain in href.lower() for domain in ['vk.com', 't.me', 'instagram', 'wa.me', 'telegram']):
                                    org.website = href.split('?')[0]
                                    break
                        except Exception:
                            continue
                    
                    if org.website:
                        break
                        
            except Exception as e:
                logger.debug(f"Ошибка извлечения сайта: {e}")
            
            # === ИЗВЛЕЧЕНИЕ СОЦСЕТЕЙ ===
            try:
                all_links = self.page.locator("a[href]")
                count = await all_links.count()
                
                for i in range(count):
                    try:
                        href = await all_links.nth(i).get_attribute("href") or ""
                        
                        if 'vk.com' in href.lower() and not org.vk:
                            org.vk = href
                        elif ('t.me' in href.lower() or 'telegram.org' in href.lower()) and not org.telegram:
                            org.telegram = href
                        elif 'instagram.com' in href.lower() and not org.instagram:
                            org.instagram = href
                        elif 'max.ru' in href.lower() and not org.max_social:
                            org.max_social = href
                    except Exception:
                        continue
                        
            except Exception as e:
                logger.debug(f"Ошибка извлечения соцсетей: {e}")
            
            # === ИЗВЛЕЧЕНИЕ EMAIL ===
            try:
                email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
                email_match = re.search(email_pattern, html)
                if email_match:
                    org.email = email_match.group(0)
            except Exception as e:
                logger.debug(f"Ошибка извлечения email: {e}")
            
            # === ИЗВЛЕЧЕНИЕ ГРАФИКА РАБОТЫ ===
            try:
                hours_selectors = [
                    ".business-working-hours-view",
                    "[class*='working-hours']",
                    "[class*='opening-hours']",
                    ".orgpage-working-hours-view",
                    "[class*='schedule']",
                ]
                hours_el = safe_get_selector(self.page, hours_selectors)
                if hours_el:
                    org.working_hours = clean_text(await hours_el.inner_text())
            except Exception as e:
                logger.debug(f"Ошибка извлечения графика: {e}")
            
            # === ИЗВЛЕЧЕНИЕ РУБРИК И ПОДРАЗДЕЛОВ ===
            try:
                rubric_selectors = [
                    "a[href*='/category/']",
                    "[class*='category'] a",
                    ".business-card-view__breadcrumbs a",
                    "[class*='breadcrumb'] a",
                ]
                
                rubrics_list = []
                subsections_list = []
                
                for selector in rubric_selectors:
                    rubric_els = self.page.locator(selector)
                    count = await rubric_els.count()
                    
                    for i in range(min(count, 10)):
                        try:
                            text = clean_text(await rubric_els.nth(i).inner_text())
                            if text:
                                if i == 0:
                                    subsections_list.append(text)
                                else:
                                    rubrics_list.append(text)
                        except Exception:
                            continue
                    
                    if rubrics_list or subsections_list:
                        break
                
                org.subsections = ", ".join(unique_list(subsections_list))
                org.rubrics = ", ".join(unique_list(rubrics_list))
                
            except Exception as e:
                logger.debug(f"Ошибка извлечения рубрик: {e}")
            
            # === ИЗВЛЕЧЕНИЕ ЛОГОТИПА ===
            try:
                logo_selectors = [
                    ".orgpage-header-view__logo img",
                    "[class*='logo'] img",
                    ".business-card-view__logo img",
                ]
                logo_el = safe_get_selector(self.page, logo_selectors)
                if logo_el:
                    org.logo_url = await logo_el.get_attribute("src") or ""
            except Exception as e:
                logger.debug(f"Ошибка извлечения логотипа: {e}")
            
            # === ИЗВЛЕЧЕНИЕ ГАЛЕРЕИ (до 5 изображений) ===
            try:
                gallery_selectors = [
                    ".gallery-view__image img",
                    "[class*='photo'] img",
                    ".business-gallery-view img",
                    "[class*='carousel'] img",
                ]
                
                images = []
                for selector in gallery_selectors:
                    img_els = self.page.locator(selector)
                    count = await img_els.count()
                    
                    for i in range(min(count, 10)):
                        try:
                            src = await img_els.nth(i).get_attribute("src") or ""
                            if src and src not in images and 'placeholder' not in src.lower():
                                images.append(src)
                                if len(images) >= 5:
                                    break
                        except Exception:
                            continue
                    
                    if len(images) >= 5:
                        break
                
                org.gallery_images = ", ".join(images[:5])
                
            except Exception as e:
                logger.debug(f"Ошибка извлечения галереи: {e}")
            
            # === ИЗВЛЕЧЕНИЕ ОСОБЕННОСТЕЙ (FEATURES) ===
            try:
                feature_selectors = [
                    ".card-feature-view",
                    "[class*='feature']",
                    ".business-feature-view",
                    "[class*='amenity']",
                ]
                
                for selector in feature_selectors:
                    feature_els = self.page.locator(selector)
                    count = await feature_els.count()
                    
                    for i in range(count):
                        try:
                            feature_text = clean_text(await feature_els.nth(i).inner_text())
                            
                            if feature_text and ':' in feature_text:
                                parts = feature_text.split(':', 1)
                                key = clean_text(parts[0])
                                value = clean_text(parts[1]) if len(parts) > 1 else ""
                                
                                if key and value:
                                    # Добавляем в динамические колонки
                                    if key not in dynamic_features_columns:
                                        dynamic_features_columns.append(key)
                                        logger.debug(f"➕ Новая особенность: {key}")
                                    
                                    org.features[key] = value
                                    
                        except Exception:
                            continue
                        
            except Exception as e:
                logger.debug(f"Ошибка извлечения особенностей: {e}")
            
            # === ИЗВЛЕЧЕНИЕ РЕГИОНА ===
            org.region = extract_region_from_address(org.address) or self.city
            
            return org
            
        except Exception as e:
            logger.error(f"❌ Ошибка парсинга страницы {url}: {e}")
            return None
    
    def _init_csv(self):
        """Инициализирует CSV файл с заголовками."""
        file_exists = os.path.exists(OUTPUT_CSV)
        
        # Формируем полный список колонок
        all_columns = BASE_COLUMNS + dynamic_features_columns
        
        self.csv_file = open(OUTPUT_CSV, 'a', newline='', encoding='utf-8-sig')
        self.csv_writer = csv.DictWriter(self.csv_file, fieldnames=all_columns)
        
        if not file_exists:
            self.csv_writer.writeheader()
            logger.info(f"📄 Создан новый CSV файл: {OUTPUT_CSV}")
            logger.info(f"Колонки: {len(all_columns)} (базовых: {len(BASE_COLUMNS)}, особенностей: {len(dynamic_features_columns)})")
    
    def _save_to_csv(self, org: Organization):
        """
        Сохраняет организацию в CSV файл.
        
        Args:
            org: Объект организации для сохранения
        """
        if self.csv_file is None or self.csv_writer is None:
            self._init_csv()
        
        try:
            # Проверка на дубли перед записью
            if self._is_duplicate(org):
                logger.debug(f"⏭️  Пропуск дубля: {org.name}")
                self.stats["skipped_duplicates"] += 1
                return
            
            row_data = org.to_dict()
            self.csv_writer.writerow(row_data)
            self.csv_file.flush()  # Гарантируем запись на диск
            
        except Exception as e:
            logger.error(f"❌ Ошибка записи в CSV: {e}")
    
    def _is_duplicate(self, org: Organization) -> bool:
        """
        Проверяет является ли организация дублем.
        
        Args:
            org: Объект организации
            
        Returns:
            True если дубль, False иначе
        """
        if not os.path.exists(OUTPUT_CSV):
            return False
        
        try:
            with open(OUTPUT_CSV, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Проверка по URL
                    if row.get('org_url') == org.org_url:
                        return True
                    # Проверка по названию + адресу
                    if row.get('name') == org.name and row.get('address') == org.address:
                        return True
        except Exception:
            pass
        
        return False
    
    def close_csv(self):
        """Закрывает CSV файл."""
        if self.csv_file:
            self.csv_file.close()
            self.csv_file = None
            logger.debug("CSV файл закрыт")
    
    def get_stats(self) -> Dict[str, int]:
        """Возвращает статистику парсинга."""
        return self.stats.copy()


# ─────────────────────────────────────────────────────────────────────────────
# ГЛАВНЫЙ КЛАСС ПАРСЕРА
# ─────────────────────────────────────────────────────────────────────────────

class YandexMapsUltimateParser:
    """
    Основной класс парсера Яндекс.Карт.
    
    Реализует двухэтапную стратегию обхода лимитов:
    1. Сбор всех рубрик города
    2. Поиск по каждой рубрике отдельно
    
    Это позволяет обойти ограничение в 500 результатов на запрос
    и собрать тысячи организаций.
    """
    
    def __init__(self):
        """Инициализирует парсер."""
        self.browser = None
        self.context = None
        self.page = None
        self.start_time = None
    
    async def initialize(self):
        """
        Запускает браузер и инициализирует все компоненты.
        
        Настройки браузера:
        - Chromium с аргументами для обхода детекции
        - Stealth режим через playwright-stealth
        - Пользовательский User-Agent
        - Русская локаль
        """
        setup_logger()
        
        self.start_time = datetime.now()
        
        logger.info("=" * 70)
        logger.info("🚀 YANDEX MAPS ULTIMATE PARSER v2.0")
        logger.info("=" * 70)
        logger.info(f"📍 Город: {CITY}")
        logger.info(f"📊 Макс. организаций: {MAX_ORGANIZATIONS if MAX_ORGANIZATIONS > 0 else 'без ограничений'}")
        logger.info(f"📁 Выходной файл: {OUTPUT_CSV}")
        logger.info(f"🔍 Режим отладки: {'включён' if DEBUG_MODE else 'выключен'}")
        logger.info(f"⏱️  Задержка: {MIN_DELAY}-{MAX_DELAY} сек")
        logger.info("=" * 70)
        
        # Запуск Playwright
        playwright = await async_playwright().start()
        
        # Запуск Chromium с настройками stealth
        logger.info("🌐 Запуск браузера Chromium...")
        self.browser = await playwright.chromium.launch(
            headless=HEADLESS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-dev-shm-usage",
                "--disable-software-rasterizer",
                "--disable-gpu-sandbox",
            ]
        )
        
        # Создание контекста с настройками
        self.context = await self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        )
        
        self.page = await self.context.new_page()
        
        # Применение stealth для обхода детекции бота
        await stealth_instance.apply_stealth_async(self.page)
        
        # Дополнительная маскировка через CDP
        await self.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['ru-RU', 'ru', 'en-US', 'en']
            });
        """)
        
        logger.success("✅ Браузер запущен и настроен (stealth mode активен)")
    
    async def run(self):
        """
        Запускает полный цикл парсинга.
        
        Этапы:
        1. Инициализация браузера
        2. Сбор рубрик (Этап 1)
        3. Поиск по каждой рубрике (Этап 2)
        4. Сохранение результатов
        5. Вывод статистики
        """
        await self.initialize()
        
        try:
            # ═══════════════════════════════════════════════════════════════
            # ЭТАП 1: СБОР РУБРИК
            # ═══════════════════════════════════════════════════════════════
            logger.info("\n" + "=" * 70)
            logger.info("📋 ЭТАП 1: СБОР РУБРИКАТОРА ГОРОДА")
            logger.info("=" * 70)
            
            rubric_parser = RubricParser(self.page)
            rubrics = await rubric_parser.collect_all_rubrics(CITY)
            
            if not rubrics:
                logger.error("❌ Не удалось собрать рубрики. Завершение работы.")
                return
            
            logger.info(f"📋 Всего рубрик для обработки: {len(rubrics)}")
            logger.info(f"Примеры: {', '.join(rubrics[:5])}")
            
            # ═══════════════════════════════════════════════════════════════
            # ЭТАП 2: ПОИСК ПО КАЖДОЙ РУБРИКЕ
            # ═══════════════════════════════════════════════════════════════
            logger.info("\n" + "=" * 70)
            logger.info("🔍 ЭТАП 2: ПОИСК ПО РУБРИКАМ")
            logger.info("=" * 70)
            
            org_parser = OrganizationParser(self.page, CITY)
            
            total_orgs = 0
            successful_rubrics = 0
            
            for idx, rubric in enumerate(rubrics, 1):
                # Проверка достижения лимита
                if MAX_ORGANIZATIONS > 0 and total_orgs >= MAX_ORGANIZATIONS:
                    logger.info("🎯 Достигнут лимит организаций")
                    break
                
                logger.info(f"\n{'─' * 70}")
                logger.info(f"Рубрика {idx}/{len(rubrics)}: {rubric}")
                logger.info(f"{'─' * 70}")
                
                # Поиск по рубрике
                orgs_count = await org_parser.search_by_rubric(rubric)
                total_orgs += orgs_count
                
                if orgs_count > 0:
                    successful_rubrics += 1
                
                logger.success(f"✅ По рубрике '{rubric}' собрано: {orgs_count} организаций")
                logger.info(f"📊 ВСЕГО собрано: {total_orgs} организаций")
                
                # Пауза между рубриками для снижения нагрузки
                if idx < len(rubrics):
                    pause_min = 5
                    pause_max = 10
                    logger.info(f"⏸️  Пауза {pause_min}-{pause_max} сек перед следующей рубрикой...")
                    await human_delay(pause_min, pause_max)
            
            # Закрываем CSV файл
            org_parser.close_csv()
            
            # ═══════════════════════════════════════════════════════════════
            # ФИНАЛЬНАЯ СТАТИСТИКА
            # ═══════════════════════════════════════════════════════════════
            elapsed = datetime.now() - self.start_time
            
            logger.success("\n" + "=" * 70)
            logger.success("🎉 ПАРСИНГ ЗАВЕРШЕН УСПЕШНО!")
            logger.success("=" * 70)
            logger.success(f"⏱️  Общее время работы: {elapsed}")
            logger.success(f"📊 Всего обработано рубрик: {successful_rubrics}/{len(rubrics)}")
            logger.success(f"🏢 Всего собрано организаций: {total_orgs}")
            logger.success(f"⏭️  Пропущено дублей: {org_parser.stats['skipped_duplicates']}")
            logger.success(f"❌ Ошибок парсинга: {org_parser.stats['errors']}")
            logger.success(f"📁 Результаты сохранены: {OUTPUT_CSV}")
            logger.success(f"📝 Особенности (динамические колонки): {len(dynamic_features_columns)}")
            
            if dynamic_features_columns:
                logger.success(f"   Список: {', '.join(dynamic_features_columns[:10])}")
            
            logger.success("=" * 70)
            
        except KeyboardInterrupt:
            logger.warning("\n⚠️  Парсинг прерван пользователем (Ctrl+C)")
        except Exception as e:
            logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
            raise
        finally:
            await self.cleanup()
    
    async def cleanup(self):
        """Останавливает браузер и освобождает ресурсы."""
        if self.browser:
            await self.browser.close()
            logger.info("👋 Браузер закрыт")


# ─────────────────────────────────────────────────────────────────────────────
# ТОЧКА ВХОДА
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    """Главная функция запуска парсера."""
    parser = YandexMapsUltimateParser()
    await parser.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⚠️  Работа прервана пользователем")
        sys.exit(0)
    except Exception as e:
        logger.error(f"💥 Фатальная ошибка: {e}")
        sys.exit(1)
