# Yandex Maps Parser 🗺️

*Читать на других языках: [English](README.en.md)*

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-000000?logo=flask&logoColor=white)
![Selenium](https://img.shields.io/badge/Selenium-43B02A?logo=selenium&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow)

**Парсер организаций с Яндекс.Карт** с веб-интерфейсом. Собирает бизнесы по городу, району и категории (название, рейтинг, телефон, адрес, сайт и т.д.) и выгружает результат в Excel.

## Возможности

- **Веб-интерфейс на Flask** — задать город, район и категорию, следить за логами сбора в реальном времени.
- **Сбор через Selenium** — эмуляция скролла ленты, обход динамической подгрузки.
- **Ручная обработка капчи** — при появлении smartcaptcha парсер ставит сбор на паузу и ждёт, пока вы решите её в окне браузера.
- **Гибкое управление** — регулировка скорости, величины скролла, остановка на лету.
- **Хранение в SQLite** и **экспорт в Excel** (`.xlsx`) через pandas + openpyxl.
- Подсказки по популярным городам, районам и категориям.

## Стек

Python, Flask, Selenium, BeautifulSoup4, pandas, openpyxl, SQLite.

## Структура проекта

```
scrapingYandexMap/
├─ app.py                 # Flask-приложение: маршруты, логи, экспорт, БД
├─ scraper.py             # логика сбора на Selenium + обработка капчи
├─ city_districts.json    # справочник городов и районов
├─ templates/index.html   # веб-интерфейс
├─ requirements.txt
└─ README.md
```

## Запуск

```bash
python -m venv venv
venv\Scripts\activate            # Windows (Linux/macOS: source venv/bin/activate)
pip install -r requirements.txt
python app.py
```

Затем откройте <http://127.0.0.1:5000> в браузере.

> Для работы Selenium нужен установленный Google Chrome. Драйвер современные версии Selenium подхватывают автоматически (Selenium Manager).

## Как пользоваться

1. Укажите город, район (опционально) и категорию бизнеса.
2. Запустите сбор и следите за логами.
3. Если появилась капча — решите её в открывшемся окне браузера, парсер продолжит сам.
4. По завершении выгрузите результат в Excel.

## Лицензия

[MIT](LICENSE)

> Проект создан в образовательных целях. Соблюдайте условия использования Яндекс.Карт и законодательство о защите данных при сборе информации.
