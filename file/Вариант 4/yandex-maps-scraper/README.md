# Yandex Maps Scraper

Локальный scraper-first MVP для сбора организаций из веб-версии Яндекс Карт через Playwright с UI, backend API, SQLite, jobs и экспортом.

## Структура

```text
yandex-maps-scraper/
  apps/
    frontend/
      src/
        api/
        components/
        pages/
        store/
  services/
    backend/
      alembic/
      app/
        api/
        core/
        db/
        exports/
        models/
        repositories/
        schemas/
        scraper/
          browser/
          jobs/
          pages/
          parsers/
          selectors/
          utils/
        services/
      tests/
  data/
    artifacts/
    exports/
    logs/
  .env.example
  docker-compose.yml
  README.md
```

## Что реализовано

- Monorepo-структура под frontend/backend/data.
- FastAPI backend с API для `jobs`, `organizations`, `category-presets`, `exports`, `settings`.
- SQLAlchemy-модели `SearchJob`, `Organization`, `JobOrganizationLink`, `CategoryPreset`.
- SQLite-подключение и стартовая Alembic migration.
- Pydantic-схемы для создания job, чтения сущностей и экспорта.
- Scraper pipeline с page-object pattern:
  - `browser/session.py`
  - `pages/search_page.py`
  - `pages/results_panel.py`
  - `pages/org_card_page.py`
  - `selectors/yandex_maps.py`
- Стоп-условия MVP:
  - `max_results`
  - `max_scrolls`
  - несколько scroll без роста
  - captcha/manual intervention
- Дедупликация:
  - `source_org_id`
  - fallback `title + address_full`
  - fallback `title + phone`
- Экспорт в CSV и JSONL.
- Логи job, screenshot при ошибке, опциональное сохранение raw HTML.
- React + TypeScript + Vite UI со страницами:
  - Jobs
  - New Job
  - Organizations
  - Organization Details
  - Settings
  - Exports

## Что еще нужно до полноценно рабочего scraper MVP

- Довести реальные селекторы Яндекс Карт на боевой разметке и стабилизировать навигацию по DOM.
- Разделить request DB session и worker DB session через отдельный job queue/service layer еще чище.
- Добавить polling/streaming прогресса jobs на frontend.
- Добавить фильтры, пагинацию и сортировку в UI.
- Поддержать остановку job из интерфейса.
- Добавить тесты на repositories, services и scraper parsers.
- Инициализировать и прогнать `playwright install`.
- Прогнать миграции Alembic штатно вместо `create_all` как основного режима запуска.

## Локальный запуск

### Backend

```bash
cd services/backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
alembic upgrade head
uvicorn app.main:app --reload
```

### Frontend

```bash
cd apps/frontend
npm install
npm run dev
```

### Docker Compose

```bash
copy .env.example .env
docker compose up --build
```

## Ключевые файлы

- Backend entrypoint: `services/backend/app/main.py`
- API routes: `services/backend/app/api/routes/`
- ORM models: `services/backend/app/models/`
- Job runner: `services/backend/app/scraper/jobs/search_job_runner.py`
- Selectors: `services/backend/app/scraper/selectors/yandex_maps.py`
- Exporters: `services/backend/app/exports/`
- Frontend app: `apps/frontend/src/App.tsx`
- Job form: `apps/frontend/src/components/JobForm.tsx`

