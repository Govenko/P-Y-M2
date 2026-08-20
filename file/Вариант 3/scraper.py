import re
import time
import random
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains

def clean_rating(text):
    if not text: return "0.0"
    match = re.search(r'(\d(?:[.,]\d)?)', text)
    if match:
        val = match.group(1).replace(',', '.')
        if '.' not in val: val += ".0"
        return val
    return "0.0"

def check_for_captcha(driver, emit_status, app_state_getter):
    is_captcha = "Ой!" in driver.title or "smartcaptcha" in driver.current_url or "showcaptcha" in driver.current_url
    if not is_captcha:
        try:
            if driver.find_elements(By.CSS_SELECTOR, "#smart-captcha, .smart-captcha, [id*='captcha']"):
                is_captcha = True
        except: pass

    if is_captcha:
        emit_status("⚠️ КАПЧА! Ждем решения в окне...")
        while app_state_getter().get('captcha_wait', False):
            if app_state_getter().get('should_stop', False): return False
            time.sleep(2)
        if not ("Ой!" in driver.title or "smartcaptcha" in driver.current_url):
            emit_status("✅ Капча решена! Продолжаем...")
            return True
    return False

def extract_org_data(driver, city, district, category):
    data = {
        'Название': 'Неизвестно', 'Категория': category, 'Район': district,
        'Адрес': 'Нет', 'Телефон': 'Нет', 'Сайт': 'Нет сайта',
        'Рейтинг': '0.0', 'Отзывы': '0', 'URL': driver.current_url.split('?')[0],
        'VK': 'Нет', 'Telegram': 'Нет', 'WhatsApp': 'Нет'
    }
    try:
        # Название
        h1s = driver.find_elements(By.CSS_SELECTOR, "h1.orgpage-header-view__header, h1[class*='header']")
        if h1s: data['Название'] = h1s[0].text.strip()
        
        # Рейтинг
        r_els = driver.find_elements(By.CSS_SELECTOR, ".business-rating-badge-view__rating-text, .business-rating-badge-view__rating-value")
        if r_els: data['Рейтинг'] = clean_rating(r_els[0].text)
        
        # Отзывы
        c_els = driver.find_elements(By.CSS_SELECTOR, ".business-header-rating-view__text, .business-rating-amount-view")
        if c_els: data['Отзывы'] = ''.join(filter(str.isdigit, c_els[0].text)) or "0"
        
        # Адрес и Телефон
        addr = driver.find_elements(By.CSS_SELECTOR, ".business-contacts-view__address, .address-line")
        if addr: data['Адрес'] = addr[0].text.strip()
        
        phones = driver.find_elements(By.CSS_SELECTOR, "[class*='phone-number']")
        if phones and phones[0].text.strip() and "Показать" not in phones[0].text: 
            data['Телефон'] = phones[0].text.strip()
        
        # Ссылки
        for l in driver.find_elements(By.TAG_NAME, "a"):
            try:
                href = l.get_attribute('href')
                if not href: continue
                low = href.lower()
                
                if low.startswith('tel:'):
                    if data['Телефон'] == 'Нет': 
                        data['Телефон'] = href.replace('tel:', '').strip()
                    continue
                
                if 'yandex' in href or 'ya.ru' in href: continue
                
                if 'vk.com' in low: data['VK'] = href
                elif 't.me' in low: data['Telegram'] = href
                elif 'wa.me' in low or 'whatsapp' in low: data['WhatsApp'] = href
                elif 'http' in low and data['Сайт'] == "Нет сайта" and ('.ru' in low or '.com' in low): 
                    data['Сайт'] = href
            except: continue
            
        if data['WhatsApp'] == "Нет" and data['Телефон'] != "Нет":
            pure = ''.join(filter(str.isdigit, data['Телефон']))
            if len(pure) >= 10: data['WhatsApp'] = f"https://wa.me/7{pure[-10:]}"
    except Exception as e:
        print(f"DEBUG Error extraction: {e}")
    return data

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0"
]
RESOLUTIONS = ["1920,1080", "1366,768", "1440,900", "1536,864", "1600,900"]

def run_scraper(city, district, category, emit_status, app_state_getter, limit=50, speed='medium'):
    options = webdriver.ChromeOptions()
    
    # Рандомизация отпечатков браузера
    ua = random.choice(USER_AGENTS)
    res = random.choice(RESOLUTIONS)
    
    options.add_argument(f'--window-size={res}')
    options.add_argument(f'--user-agent={ua}')
    
    # Базовый антидетект
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    
    driver = webdriver.Chrome(options=options)
    
    # Глубокая маскировка вебдрайвера через CDP
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    
    
    def my_sleep(sec):
        m = app_state_getter().get('speed_multiplier', 1.0)
        target = time.time() + (sec / (m if m > 0.01 else 1.0))
        while time.time() < target:
            if app_state_getter().get('should_stop', False): return
            time.sleep(0.5)
            
    processed_urls = set()
    active_tabs = {} 
    total_found = 0
    last_found_time = time.time()
    refresh_count = 0
    MAX_TABS = 3 
    
    try:
        query_tail = f"{district} {category}".strip()
        url = f"https://yandex.ru/maps/?text={city} {query_tail}"
        driver.get(url)
        emit_status(f"🛰️ РЕЖИМ МАКСИМАЛЬНОЙ ВИЗУАЛИЗАЦИИ. Сбор: {city} {query_tail}")
        my_sleep(6)
        
        main_handle = driver.current_window_handle
        last_activity = time.time()
        last_scroll_msg = time.time()

        while total_found < limit:
            if app_state_getter().get('should_stop', False):
                emit_status("🛑 Остановка...")
                break

            check_for_captcha(driver, emit_status, app_state_getter)
            
            snippets = driver.find_elements(By.CSS_SELECTOR, "div.search-snippet-view__body")
            batch = []
            for s in snippets:
                if app_state_getter().get('should_stop', False): break
                try:
                    for a in s.find_elements(By.TAG_NAME, "a"):
                        href = a.get_attribute('href')
                        if href and "/org/" in href:
                            # Извлекаем чистую ссылку на главную страницу организации (без /gallery, /reviews и т.д.)
                            match = re.search(r'(https://yandex\.ru/maps/org/[^/]+/\d+)', href.split('?')[0])
                            if match:
                                u = match.group(1) + "/"
                                if u not in processed_urls:
                                    batch.append(u); processed_urls.add(u)
                                    break
                except: continue
                if len(processed_urls) >= limit: break

            while len(active_tabs) < MAX_TABS and batch and not app_state_getter().get('should_stop', False):
                u = batch.pop(0)
                driver.execute_script(f"window.open('{u}', '_blank');")
                my_sleep(1.5) # Пауза больше для подгрузки картинок
                for h in driver.window_handles:
                    if h != main_handle and h not in active_tabs:
                        active_tabs[h] = time.time(); break

            for h in list(active_tabs.keys()):
                if app_state_getter().get('should_stop', False): break
                try:
                    driver.switch_to.window(h)
                    check_for_captcha(driver, emit_status, app_state_getter)

                    if time.time() - active_tabs[h] > 50: # Таймаут больше
                        try: driver.close()
                        except: pass
                        if h in active_tabs: del active_tabs[h]
                        driver.switch_to.window(main_handle); continue

                    if driver.find_elements(By.CSS_SELECTOR, "h1[class*='header']"):
                        d = extract_org_data(driver, city, district or "Не указан", category)
                        emit_status(f"✓ {d['Название']} (⭐{d['Рейтинг']}, {d['Отзывы']} отз.)", data=d)
                        total_found += 1; last_activity = time.time()
                        last_found_time = time.time()
                        refresh_count = 0
                        driver.close(); del active_tabs[h]
                        driver.switch_to.window(main_handle)
                except Exception as e:
                    if h in active_tabs: del active_tabs[h]
                    try: driver.switch_to.window(main_handle)
                    except: pass

            if not batch and not active_tabs and not app_state_getter().get('should_stop', False):
                driver.switch_to.window(main_handle)
                
                try:
                    # Умный поиск: если появилась кнопка обновления области на карте, обязательно жмем
                    search_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Искать здесь') or contains(., 'этой области') or contains(., 'поиск')]")
                    clicked_search = False
                    for b in search_btns:
                        if b.is_displayed():
                            driver.execute_script("arguments[0].click();", b)
                            emit_status("🔄 Обновляем область поиска (Искать здесь)...")
                            my_sleep(3.5)
                            last_activity = time.time()  # Даем больше времени на парсинг новых резов
                            clicked_search = True
                            break
                    
                    # Если зависли (нет новых лидов больше 25 секунд) двигаем/зумим карту, чтобы она подгрузила новые
                    if not clicked_search and (time.time() - last_activity > 25):
                        zoom_ins = driver.find_elements(By.XPATH, "//button[contains(@class, 'zoom-in') or contains(@aria-label, 'риблизить') or contains(@aria-label, 'величить')]")
                        zoom_outs = driver.find_elements(By.XPATH, "//button[contains(@class, 'zoom-out') or contains(@aria-label, 'тдалить') or contains(@aria-label, 'меньшить')]")
                        
                        btns = []
                        if zoom_ins: btns.append(zoom_ins[0])
                        if zoom_outs: btns.append(zoom_outs[0])
                        
                        # 50% шанс сделать зум, 50% шанс просто сдвинуть карту (Pan)
                        if random.random() < 0.5 and btns:
                            btn = random.choice(btns)
                            action_name = "Приближение" if btn in zoom_ins else "Отдаление"
                            driver.execute_script("arguments[0].click();", btn)
                            emit_status(f"🗺️ {action_name} карты для новых лидов...")
                        else:
                            map_canvas = driver.find_elements(By.CSS_SELECTOR, "canvas")
                            if map_canvas:
                                try:
                                    ac = ActionChains(driver)
                                    x_off = random.choice([200, -200, 300, -300])
                                    y_off = random.choice([200, -200, 300, -300])
                                    
                                    ac.move_to_element(map_canvas[0]).click_and_hold()
                                    
                                    # Плавный свайп (настраиваемый через map_speed)
                                    m_speed = app_state_getter().get('map_speed', 1.0)
                                    steps = max(2, int(15 / m_speed))
                                    pause_time = 0.015 / m_speed
                                    
                                    step_x = x_off / steps
                                    step_y = y_off / steps
                                    for _ in range(steps):
                                        ac.move_by_offset(step_x, step_y).pause(max(0.001, pause_time))
                                        
                                    ac.release().perform()
                                    emit_status("🗺️ Смещение карты для новых лидов...")
                                except Exception as e: pass
                            elif btns:
                                btn = random.choice(btns)
                                action_name = "Приближение" if btn in zoom_ins else "Отдаление"
                                driver.execute_script("arguments[0].click();", btn)
                                emit_status(f"🗺️ {action_name} карты для новых лидов...")

                        # Пауза после действий карты (тоже регулируется)
                        my_sleep(2 / app_state_getter().get('map_speed', 1.0))
                        last_activity = time.time()
                except: pass
                
                try:
                    scr = driver.find_elements(By.CSS_SELECTOR, ".scroll__container")
                    scAmt = app_state_getter().get('scroll_amount', 900)
                    if scr:
                        driver.execute_script(f"arguments[0].scrollTo({{top: arguments[0].scrollTop + {scAmt}, behavior: 'smooth'}});", scr[0])
                    else:
                        driver.execute_script(f"window.scrollBy({{top: {scAmt}, behavior: 'smooth'}});")
                        
                    my_sleep(3.5)
                    if time.time() - last_scroll_msg > 20:
                        emit_status(f"⏳ Скроллинг... Собрано: {total_found}")
                        last_scroll_msg = time.time()
                    
                    if time.time() - last_found_time > 65:
                        if refresh_count < 2:
                            emit_status("⚠️ Обнаружена вечная загрузка или тупик. Пытаюсь сбросить кеш (перезагрузка страницы)...")
                            try: driver.refresh()
                            except: pass
                            my_sleep(5)
                            last_found_time = time.time() + 15
                            last_activity = time.time()
                            refresh_count += 1
                        else:
                            emit_status("🛑 Новых лидов слишком долго нет даже после перезагрузок. Достигнут конец списка.")
                            break
                except:
                    driver.execute_script("window.scrollBy(0, 1200);")
                    my_sleep(3)
                    if time.time() - last_scroll_msg > 20:
                        emit_status(f"⏳ Скроллинг... Собрано: {total_found}")
                        last_scroll_msg = time.time()
                    if time.time() - last_activity > 110: break
            
            my_sleep(1)

    except Exception as e:
        emit_status(f"❌ Системная ошибка: {str(e)}")
    finally:
        emit_status(f"🏁 Сбор завершен. Итог: {total_found}")
        driver.quit()
