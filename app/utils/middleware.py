import time
from collections import defaultdict, deque
from starlette.responses import HTMLResponse


class RequestGuard:
    """Bound body size before multipart parsing; set safe response defaults."""

    def __init__(self, app, maximum, secure):
        self.app, self.maximum, self.secure = app, maximum, secure
        self.events = defaultdict(deque)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        headers = {k.lower(): v for k, v in scope["headers"]}
        if path.startswith("/auth/telegram/start"):
            key = (scope.get("client") or ("unknown",))[0]
            stamp = time.monotonic()
            events = self.events[key]
            while events and stamp - events[0] > 60:
                events.popleft()
            if len(events) >= 20:
                return await self.error(scope, receive, send, 429, "Слишком много попыток. Подождите минуту.")
            events.append(stamp)
            if len(self.events) > 10000:
                self.events = defaultdict(deque, {k: v for k, v in self.events.items() if v and stamp - v[-1] < 60})
        if scope["method"] in {"POST", "PUT", "PATCH"}:
            try:
                length = int(headers.get(b"content-length", b"0"))
            except ValueError:
                length = self.maximum + 1
            if length > self.maximum:
                return await self.error(scope, receive, send, 413, "Файл слишком большой. Максимальный ZIP — 30 MB.")
            chunks, size = [], 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > self.maximum:
                    return await self.error(scope, receive, send, 413, "Превышен максимальный размер запроса.")
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break
            done = False

            async def replay():
                nonlocal done
                if not done:
                    done = True
                    return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
                return await receive()

            actual_receive = replay
        else:
            actual_receive = receive

        async def secure_send(message):
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message["headers"]}
                defaults = {
                    b"x-content-type-options": b"nosniff",
                    b"x-frame-options": b"DENY",
                    b"referrer-policy": b"strict-origin-when-cross-origin",
                    b"permissions-policy": b"camera=(), microphone=(), geolocation=()",
                    b"content-security-policy": b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https: data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
                }
                if not path.startswith("/static/"):
                    defaults[b"cache-control"] = b"private, no-store"
                if self.secure:
                    defaults[b"strict-transport-security"] = b"max-age=31536000"
                message["headers"].extend((k, v) for k, v in defaults.items() if k not in existing)
            await send(message)

        await self.app(scope, actual_receive, secure_send)

    async def error(self, scope, receive, send, status, text):
        html = f'<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/app.css"><title>Ошибка {status}</title><main class="container error-page"><div class="eyebrow">NEXVIRO CODE BATTLE</div><h1>{status}</h1><p>{text}</p><a class="button" href="/">На главную</a></main></html>'
        return await HTMLResponse(html, status_code=status)(scope, receive, send)
