import os
import sqlite3
import threading
import time
import io
from flask import Flask, render_template, request, jsonify, send_file
import pandas as pd
from scraper import run_scraper

app = Flask(__name__)

# Простые глобальконые переменные
scraper_logs = []
scraper_is_running = False
current_session_id = None
app_state = {
    "should_stop": False,
    "captcha_wait": False,
    "speed_multiplier": 1.0,
    "scroll_amount": 900
}

# Список крупных городов для автоподстановки (пример)
CITY_SUGGESTIONS = [
    "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань", 
    "Нижний Новгород", "Челябинск", "Самара", "Омск", "Ростов-на-Дону", 
    "Уфа", "Красноярск", "Воронеж", "Пермь", "Волгоград", "Краснодар", 
    "Саратов", "Тюмень", "Тольятти", "Ижевск", "Барнаул", "Ульяновск",
    "Иркутск", "Хабаровск", "Махачкала", "Владивосток", "Оренбург", 
    "Севастополь", "Томск", "Кемерово", "Набережные Челны", "Липецк", 
    "Киров", "Чебоксары", "Балашиха", "Краснодар", "Красногорск"
]

DISTRICT_SUGGESTIONS = [
    "Центральный", "Прикубанский", "Ленинский", "Октябрьский", "Кировский",
    "Заводской", "Советский", "Московский", "Первомайский", "Южный",
    "Северный", "Восточный", "Западный", "Фестивальный", "Юбилейный"
]

CATEGORY_SUGGESTIONS = [
    "Стоматология", "Фитнес-клуб", "Салон красоты", "Автосервис",
    "Ремонт квартир", "Маркетинговое агентство", "Цветы", "Кафе",
    "Ресторан", "Гостиница", "Магазин одежды", "Юрист", "Бухгалтер"
]

DB_NAME = 'database.db'

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS businesses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            name TEXT,
            category TEXT,
            district TEXT,
            address TEXT,
            phone TEXT,
            website TEXT,
            vk TEXT,
            telegram TEXT,
            whatsapp TEXT,
            rating TEXT,
            reviews TEXT,
            url TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions (id)
        )
    ''')
    # Миграция: Добавляем колонки, если их нет в старой версии БД
    try: cursor.execute('ALTER TABLE businesses ADD COLUMN session_id INTEGER')
    except: pass # Колонки уже есть
    try: cursor.execute('ALTER TABLE businesses ADD COLUMN district TEXT')
    except: pass
    conn.commit()
    conn.close()

def save_to_db(data, session_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO businesses 
            (session_id, name, category, district, address, phone, website, vk, telegram, whatsapp, rating, reviews, url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            session_id,
            data.get('Название', 'Неизвестно'),
            data.get('Категория', 'Нет'),
            data.get('Район', 'Центр'),
            data.get('Адрес', 'Нет'),
            data.get('Телефон', 'Нет'),
            data.get('Сайт', 'Нет сайта'),
            data.get('VK', 'Нет'),
            data.get('Telegram', 'Нет'),
            data.get('WhatsApp', 'Нет'),
            data.get('Рейтинг', '0.0'),
            data.get('Отзывы', '0'),
            data.get('URL', '')
        ))
        conn.commit()
    except Exception as e: 
        print(f"Ошибка БД при сохранении: {e}")
    finally: 
        conn.close()

def status_callback(msg, data=None):
    global scraper_logs, current_session_id, app_state
    if msg: 
        scraper_logs.append(msg); print(msg)
        # СИГНАЛ КАПЧИ ДЛЯ ФРОНТЕНДА
        if "⚠️ КАПЧА!" in msg:
            app_state["captcha_wait"] = True
            
    if data and current_session_id: save_to_db(data, current_session_id)

def background_task_queue(city, districts, categories, limit, speed):
    global scraper_is_running, current_session_id
    try:
        # Разбиваем списки на строки
        dist_list = [d.strip() for d in districts.split('\n') if d.strip()]
        if not dist_list: 
            # Если районы не указаны, пытаемся подтянуть их из встроенной БД
            try:
                import json
                with open('city_districts.json', 'r', encoding='utf-8') as f:
                    city_db = json.load(f)
                c_key = city.strip().lower()
                if c_key in city_db:
                    dist_list = city_db[c_key]
                    status_callback(f"🌍 Автоматически загружены районы для '{city}': {len(dist_list)} шт.")
                else:
                    dist_list = [""] # Если города нет в БД
                    status_callback(f"🌍 Разделение по районам выключено (город '{city}' не найден в БД, ищу целиком).")
            except:
                dist_list = [""]

        cat_list = [c.strip() for c in categories.split('\n') if c.strip()]
        
        total_tasks = len(dist_list) * len(cat_list)
        current_task = 0
        
        for cat in cat_list:
            for dist in dist_list:
                current_task += 1
                if app_state["should_stop"]: break
                
                query_str = f"{city} {dist} {cat}".strip().replace("  ", " ")
                status_callback(f"🏁 СТАРТ ЗАДАЧИ {current_task}/{total_tasks}: '{query_str}'")
                
                # Новая сессия для каждого запроса
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                cursor.execute('INSERT INTO sessions (query) VALUES (?)', (query_str,))
                current_session_id = cursor.lastrowid
                conn.commit()
                conn.close()
                
                # Запуск скрепера
                run_scraper(city, dist, cat, status_callback, lambda: app_state, limit, speed)
                
                if app_state["should_stop"]: break
                time.sleep(3) # Пауза перед следующим запросом

    except Exception as e: status_callback(f"Ошибка очереди: {str(e)}")
    finally:
        status_callback("DONE")
        scraper_is_running = False

@app.route('/')
def index(): return render_template('index.html')

@app.route('/suggest')
def suggest():
    q = request.args.get('q', '').lower()
    t = request.args.get('type', 'city')
    
    source = CITY_SUGGESTIONS if t == 'city' else (DISTRICT_SUGGESTIONS if t == 'district' else CATEGORY_SUGGESTIONS)
    matches = [s for s in source if q in s.lower()]
    return jsonify(matches[:10])

@app.route('/sessions')
def get_sessions():
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM sessions ORDER BY timestamp DESC')
        rows = cursor.fetchall()
    finally:
        conn.close()
    return jsonify([dict(row) for row in rows])

@app.route('/start', methods=['POST'])
def start_scraping():
    global scraper_is_running, app_state, scraper_logs
    if scraper_is_running: return jsonify({"error": "Скрипт уже запущен"}), 400
    
    # Очищаем терминал перед новым сбором (для красоты консоли)
    os.system('cls' if os.name == 'nt' else 'clear')
    
    scraper_logs = []
    data = request.json or {}
    city = data.get('city', 'Краснодар')
    districts = data.get('districts', '')
    categories = data.get('categories', 'стоматология')
    limit = int(data.get('limit', 50))
    speed = data.get('speed', 'medium')
    
    app_state["should_stop"] = False
    app_state["captcha_wait"] = False
    app_state["speed_multiplier"] = 1.0
    app_state["scroll_amount"] = 900
    app_state["map_speed"] = 1.0
    scraper_is_running = True
    
    thread = threading.Thread(target=background_task_queue, args=(city, districts, categories, limit, speed))
    thread.daemon = True
    thread.start()
    return jsonify({"status": "started"})

@app.route('/stop', methods=['POST'])
def stop_scraping():
    global app_state
    app_state["should_stop"] = True
    return jsonify({"status": "stopping"})

@app.route('/captcha_solved', methods=['POST'])
def captcha_solved():
    global app_state
    app_state["captcha_wait"] = False
    return jsonify({"status": "resumed"})

@app.route('/set_speed', methods=['POST'])
def set_speed():
    global app_state
    data = request.json or {}
    if 'multiplier' in data:
        app_state["speed_multiplier"] = float(data.get('multiplier', 1.0))
    if 'scroll_amount' in data:
        app_state["scroll_amount"] = int(data.get('scroll_amount', 900))
    if 'map_speed' in data:
        app_state["map_speed"] = float(data.get('map_speed', 1.0))
    return jsonify({"status": "speed_updated"})

@app.route('/status')
def get_status():
    global scraper_logs, scraper_is_running, app_state
    return jsonify({"logs": scraper_logs, "running": scraper_is_running, "captcha_wait": app_state["captcha_wait"]})

@app.route('/results')
def get_results():
    sid = request.args.get('session_id', 'all')
    try:
        conn = sqlite3.connect(DB_NAME); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
        if sid != 'all': cursor.execute('SELECT * FROM businesses WHERE session_id = ? ORDER BY timestamp DESC', (sid,))
        else: cursor.execute('SELECT * FROM businesses ORDER BY timestamp DESC')
        rows = cursor.fetchall()
    finally:
        conn.close()
    return jsonify({"results": [dict(r) for r in rows]})

@app.route('/delete_session', methods=['POST'])
def delete_session():
    data = request.json or {}
    sid = data.get('session_id')
    if sid and sid != 'all':
        try:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM businesses WHERE session_id = ?', (sid,))
            cursor.execute('DELETE FROM sessions WHERE id = ?', (sid,))
            conn.commit()
        finally:
            conn.close()
    return jsonify({"status": "deleted"})

@app.route('/clear_database', methods=['POST'])
def clear_db():
    global scraper_logs
    scraper_logs = []
    try:
        conn = sqlite3.connect(DB_NAME); cursor = conn.cursor()
        cursor.execute('DELETE FROM businesses'); cursor.execute('DELETE FROM sessions')
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "cleared"})

@app.route('/download')
def download():
    sid = request.args.get('session_id', 'all')
    try:
        conn = sqlite3.connect(DB_NAME)
        if sid != 'all': df = pd.read_sql_query('SELECT * FROM businesses WHERE session_id = ? ORDER BY timestamp DESC', conn, params=(sid,))
        else: df = pd.read_sql_query('SELECT * FROM businesses ORDER BY timestamp DESC', conn)
    finally:
        conn.close()
    if df.empty: return "Пусто", 404
    col_map = {'name': 'Название', 'category': 'Категория', 'district': 'Район', 'address': 'Адрес', 'phone': 'Телефон', 'website': 'Сайт', 'vk': 'VK', 'telegram': 'Telegram', 'whatsapp': 'WhatsApp', 'rating': 'Рейтинг', 'reviews': 'Отзывы', 'url': 'Ссылка Яндекс.Карты', 'timestamp': 'Дата сбора'}
    df = df.rename(columns=col_map)
    if 'id' in df.columns: df = df.drop(columns=['id', 'session_id'])
    
    output = io.BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="export.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__ == '__main__':
    init_db()
    app.run(host='127.0.0.1', port=5000)
