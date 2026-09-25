from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from app.db import get_db
from app.models import Participation, Payment
from app.services.errors import DomainError
from app.services.storage import Storage
from app.services.participation import create_payment
from app.services.contest_state import locked_contest, registration_open
from app.utils.security import require_user, safe_form
from app.utils.web import render, redirect
from app.config import get_settings

router = APIRouter()


def payment_context(db, participation_id, user):
    p = db.get(Participation, participation_id)
    if not p or p.user_id != user.id:
        raise DomainError("Заявка не найдена.", 404)
    c = locked_contest(db, p.contest_id)
    db.refresh(p)
    db.commit()
    pay = db.scalar(select(Payment).where(Payment.participation_id == p.id))
    return {
        "p": p,
        "c": c,
        "pay": pay,
        "can_upload": registration_open(c) and p.status in {"awaiting_payment", "rejected"},
    }


@router.get("/payments/{participation_id}")
def payment_page(request: Request, participation_id: int, user=Depends(require_user), db=Depends(get_db)):
    return render(request, "payment.html", user, **payment_context(db, participation_id, user))


@router.post("/payments/{participation_id}")
async def upload_receipt(
    request: Request, participation_id: int, user=Depends(require_user), form=Depends(safe_form), db=Depends(get_db)
):
    context = await run_in_threadpool(payment_context, db, participation_id, user)
    stored = None
    storage = Storage()
    try:
        file = form.get("receipt")
        if not isinstance(file, UploadFile) or not file.filename:
            raise DomainError("Прикрепите чек.")
        data = await file.read(get_settings().max_receipt_mb * 1024**2 + 1)
        stored = await run_in_threadpool(storage.save, data, file.filename, file.content_type, "receipts")
        pay, old = await run_in_threadpool(create_payment, db, participation_id, user, stored)
        await run_in_threadpool(db.commit)
        await run_in_threadpool(storage.delete, old)
    except DomainError as exc:
        await run_in_threadpool(db.rollback)
        if stored:
            await run_in_threadpool(storage.delete, stored.key)
        return render(request, "payment.html", user, status_code=exc.status, errors=[exc.message], **context)
    except Exception:
        await run_in_threadpool(db.rollback)
        if stored:
            await run_in_threadpool(storage.delete, stored.key)
        raise
    return redirect(
        request, f"/payments/{participation_id}", "Чек отправлен на проверку. Статус обновится автоматически."
    )
