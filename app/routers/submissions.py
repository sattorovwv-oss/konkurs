from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from app.db import get_db
from app.models import Participation, Submission
from app.services.contest_state import locked_contest, submission_open
from app.services.errors import DomainError
from app.services.storage import Storage
from app.services.submissions import submit_project
from app.schemas.forms import SubmissionForm, form_errors
from app.utils.security import require_user, safe_form
from app.utils.web import render, redirect
from app.config import get_settings

router = APIRouter()


def submission_context(db, participation_id, user):
    p = db.get(Participation, participation_id)
    if not p or p.user_id != user.id:
        raise DomainError("Участие не найдено.", 404)
    if p.status != "approved":
        raise DomainError("Сначала дождитесь подтверждения участия.", 403)
    c = locked_contest(db, p.contest_id)
    db.commit()
    sub = db.scalar(select(Submission).where(Submission.participation_id == p.id))
    return {"c": c, "p": p, "sub": sub, "can_submit": submission_open(c)}


@router.get("/submissions/{participation_id}")
def submission_page(request: Request, participation_id: int, user=Depends(require_user), db=Depends(get_db)):
    return render(request, "submission.html", user, **submission_context(db, participation_id, user))


@router.post("/submissions/{participation_id}")
async def submission_post(
    request: Request, participation_id: int, user=Depends(require_user), form=Depends(safe_form), db=Depends(get_db)
):
    context = await run_in_threadpool(submission_context, db, participation_id, user)
    stored = None
    storage = Storage()
    try:
        data = SubmissionForm.model_validate(dict(form))
        file = form.get("source")
        if isinstance(file, UploadFile) and file.filename:
            content = await file.read(get_settings().max_source_mb * 1024**2 + 1)
            stored = await run_in_threadpool(storage.save, content, file.filename, file.content_type, "submissions")
        sub, old = await run_in_threadpool(submit_project, db, participation_id, user, data, stored)
        await run_in_threadpool(db.commit)
        await run_in_threadpool(storage.delete, old)
    except (DomainError, ValidationError) as exc:
        await run_in_threadpool(db.rollback)
        if stored:
            await run_in_threadpool(storage.delete, stored.key)
        errors = form_errors(exc) if isinstance(exc, ValidationError) else [exc.message]
        return render(
            request,
            "submission.html",
            user,
            status_code=422 if isinstance(exc, ValidationError) else exc.status,
            errors=errors,
            values=dict(form),
            **context,
        )
    except Exception:
        await run_in_threadpool(db.rollback)
        if stored:
            await run_in_threadpool(storage.delete, stored.key)
        raise
    return redirect(request, f"/submissions/{participation_id}", "Проект успешно отправлен.")
