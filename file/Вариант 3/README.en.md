# Yandex Maps Parser 🗺️

*Read in other languages: [Русский](README.md)*

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-000000?logo=flask&logoColor=white)
![Selenium](https://img.shields.io/badge/Selenium-43B02A?logo=selenium&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow)

A **business parser for Yandex Maps** with a web interface. It collects businesses by city, district and category (name, rating, phone, address, website, etc.) and exports the result to Excel.

## Features

- **Flask web interface** — set city, district and category, and watch collection logs in real time.
- **Selenium-based collection** — emulates feed scrolling and handles dynamic loading.
- **Manual captcha handling** — when a smartcaptcha appears, the parser pauses and waits until you solve it in the browser window.
- **Flexible control** — adjust speed and scroll amount, stop on the fly.
- **SQLite storage** and **Excel export** (`.xlsx`) via pandas + openpyxl.
- Suggestions for popular cities, districts and categories.

## Tech stack

Python, Flask, Selenium, BeautifulSoup4, pandas, openpyxl, SQLite.

## Project structure

```
scrapingYandexMap/
├─ app.py                 # Flask app: routes, logs, export, DB
├─ scraper.py             # Selenium collection logic + captcha handling
├─ city_districts.json    # cities and districts reference
├─ templates/index.html   # web interface
├─ requirements.txt
└─ README.md
```

## Getting started

```bash
python -m venv venv
venv\Scripts\activate            # Windows (Linux/macOS: source venv/bin/activate)
pip install -r requirements.txt
python app.py
```

Then open <http://127.0.0.1:5000> in your browser.

> Selenium requires an installed Google Chrome. Modern Selenium picks up the driver automatically (Selenium Manager).

## Usage

1. Set a city, an optional district, and a business category.
2. Start collection and watch the logs.
3. If a captcha appears, solve it in the opened browser window — the parser resumes on its own.
4. When finished, export the result to Excel.

## License

[MIT](LICENSE)

> This project is for educational purposes. Respect Yandex Maps' terms of use and data-protection laws when collecting information.
