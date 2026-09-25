# Быстрый запуск NEXVIRO CODE BATTLE

Это исходный проект сайта и Telegram-бота. Сначала настройте сервер и свои ключи; после этого администратор сможет создать первый конкурс.

## 1. Подготовь бота и базу

- Telegram BotFather: токен бота, username, OIDC Client ID и Client Secret.
- BotFather → Login Widget → Allowed URLs:
  - `https://nexvirobattle.cfd`
  - `https://nexvirobattle.cfd/auth/telegram/callback`
- Neon: создай PostgreSQL базу и скопируй строку подключения.
- Добавь бота в чат для проверки чеков. Узнай числовой ID чата и свой Telegram user ID.

## 2. Настрой `.env`

Скопируй `.env.example` в `.env`, заполни пустые поля. `BOT_USERNAME` — без `@`, `PRIZE_CONTACT_URL` — полная ссылка `https://t.me/username`. В `ADMIN_TELEGRAM_IDS` укажи свой user ID. Секрет сгенерируй:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Вставь результат в `SECRET_KEY`. Файл `.env` не отправляй на GitHub.

## 3. Установи и создай таблицы

Python 3.12 или новее. Из корня проекта:

```bash
python -m pip install -r requirements.txt
python -m alembic upgrade head
python scripts/check_config.py
```

На Windows можно использовать виртуальное окружение; точные команды есть в README.

## 4. Запусти

Сайт:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 17563
```

Telegram-бот в другом окне:

```bash
python -m bot.main
```

Для одной стартовой команды Raven Host:

```bash
python scripts/run_all.py
```

Сначала выполняются миграции, затем запуск. Не запускай бота и отдельно, и через `run_all.py` одновременно.

## 5. Подключи домен

Публичный адрес должен работать по HTTPS. Порт `17563` — внутренний порт приложения. Для `https://nexvirobattle.cfd` нужен reverse proxy/привязка домена хостингом либо Cloudflare Tunnel. Одного добавления DNS недостаточно. В README есть варианты и готовые конфигурации в `deploy/`.

## 6. Создай первый конкурс

Войди на сайт через свой Telegram → Admin → Создать конкурс → проверь черновик → Опубликовать. Вводи время **Душанбе**, переводить его в UTC вручную не нужно.

После загрузки чека проверь поступление денег и нажми «Одобрить». Дальше: старт → сдача проектов → завершение приёма → оценки → публикация. Участники получат результаты и ссылки на призы.

Перед первым платным конкурсом пройди небольшой контрольный сценарий со своим ботом и тестовой заявкой. Локальные автоматические тесты не проверяют действительность твоих ключей, настройки банковской карты, домена и прав бота в чате.
