from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse
from app.config import ROOT, get_settings
from app.utils.security import csrf_token, is_admin
from app.utils.time import local_display, local_input, aware

STATUS_LABELS = {
    "draft": "Черновик",
    "scheduled": "Скоро старт",
    "running": "Идёт конкурс",
    "frozen": "Заморожен",
    "finished": "Завершён",
    "cancelled": "Отменён",
    "approved": "Участие подтверждено",
    "awaiting_payment": "Ожидает оплаты",
    "payment_pending": "Чек на проверке",
    "pending": "На проверке",
    "rejected": "Отклонён",
    "submitted": "Ожидает оценки",
    "reviewed": "Оценено",
}

templates = Jinja2Templates(directory=ROOT / "app/templates")
templates.env.filters.update(
    localtime=local_display,
    localinput=local_input,
    iso=lambda v: aware(v).isoformat() if v else "",
    money=lambda v: f"{v:,.2f}".replace(",", " ").rstrip("0").rstrip("."),
    status=lambda v: STATUS_LABELS.get(v, v),
)


def render(request, name, user=None, status_code=200, **context):
    return templates.TemplateResponse(
        request=request,
        name=name,
        context={
            "user": user,
            "is_admin": is_admin(user),
            "csrf": csrf_token(request),
            "app_name": get_settings().app_name,
            "settings": get_settings(),
            "flashes": request.session.pop("flashes", []),
            **context,
        },
        status_code=status_code,
    )


def redirect(request, url, message=None):
    if message:
        request.session["flashes"] = [message]
    return RedirectResponse(url, status_code=303)
