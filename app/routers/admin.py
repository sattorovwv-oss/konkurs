from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from app.db import get_db
from app.models import Contest, User, Participation, Payment, Submission, AuditLog, Notification
from app.schemas.forms import ContestForm, ScoreForm, form_errors
from app.services.contest_state import change_state, tick, locked_contest
from app.services.errors import DomainError
from app.services.participation import review_payment, count_approved
from app.services.ranking import ranking, get_submissions
from app.services.submissions import save_score, publish_results
from app.utils.security import require_admin, safe_form
from app.utils.web import render, redirect
from app.utils.time import now

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


@router.get("")
def dashboard(request: Request, user=Depends(require_admin), db=Depends(get_db)):
    tick(db)
    counts = {
        "Пользователи": db.scalar(select(func.count(User.id))),
        "Активные конкурсы": db.scalar(
            select(func.count(Contest.id)).where(Contest.status.in_(["scheduled", "running", "frozen"]))
        ),
        "Чеки на проверке": db.scalar(select(func.count(Payment.id)).where(Payment.status == "pending")),
        "Работы без оценки": db.scalar(select(func.count(Submission.id)).where(~Submission.scores.any())),
        "Участники": db.scalar(select(func.count(Participation.id)).where(Participation.status == "approved")),
    }
    contests = list(db.scalars(select(Contest).order_by(Contest.created_at.desc()).limit(20)))
    return render(request, "admin/dashboard.html", user, counts=counts, contests=contests, tab="dashboard")


@router.get("/contests")
def contests(request: Request, page: int = 1, user=Depends(require_admin), db=Depends(get_db)):
    page = max(1, page)
    items = list(db.scalars(select(Contest).order_by(Contest.created_at.desc()).offset((page - 1) * 20).limit(21)))
    return render(
        request, "admin/list.html", user, tab="contests", items=items[:20], page=page, has_next=len(items) > 20
    )


@router.get("/contests/new")
def new_contest(request: Request, user=Depends(require_admin)):
    return render(request, "admin/contest_form.html", user, values={}, editing=False, tab="contests")


@router.post("/contests/new")
def create_contest(request: Request, user=Depends(require_admin), form=Depends(safe_form), db=Depends(get_db)):
    try:
        data = ContestForm.model_validate(dict(form))
        if data.registration_deadline <= now():
            raise DomainError("Дата окончания регистрации должна быть в будущем.")
        if db.scalar(select(Contest.id).where(Contest.slug == data.slug)):
            raise DomainError("Этот slug уже используется.", 409)
        c = Contest(**data.model_dump(), status="draft")
        db.add(c)
        db.flush()
        db.add(AuditLog(actor_id=user.telegram_id, action="contest:created", entity_id=c.id))
        db.commit()
    except (ValidationError, DomainError, IntegrityError) as exc:
        db.rollback()
        errors = (
            form_errors(exc)
            if isinstance(exc, ValidationError)
            else [exc.message if isinstance(exc, DomainError) else "Этот slug уже используется."]
        )
        return render(
            request,
            "admin/contest_form.html",
            user,
            status_code=422,
            values=dict(form),
            errors=errors,
            editing=False,
            tab="contests",
        )
    return redirect(request, f"/admin/contests/{c.id}", "Черновик создан. Проверьте данные и опубликуйте конкурс.")


@router.get("/contests/{contest_id}")
def manage_contest(request: Request, contest_id: int, user=Depends(require_admin), db=Depends(get_db)):
    c = locked_contest(db, contest_id)
    db.commit()
    subs = get_submissions(db, c.id)
    return render(
        request,
        "admin/contest.html",
        user,
        c=c,
        subs=subs,
        rows=ranking(db, c),
        assessed=sum(bool(sub.scores) for sub in subs),
        count=count_approved(db, c.id),
        tab="contests",
    )


@router.get("/contests/{contest_id}/edit")
def edit_contest(request: Request, contest_id: int, user=Depends(require_admin), db=Depends(get_db)):
    c = db.get(Contest, contest_id)
    if not c or c.status != "draft":
        raise DomainError("Редактирование доступно только для черновика.", 409)
    return render(request, "admin/contest_form.html", user, values=c, editing=True, tab="contests")


@router.post("/contests/{contest_id}/edit")
def update_contest(
    request: Request, contest_id: int, user=Depends(require_admin), form=Depends(safe_form), db=Depends(get_db)
):
    try:
        c = locked_contest(db, contest_id)
        if c.status != "draft":
            raise DomainError("Редактирование доступно только для черновика.", 409)
        data = ContestForm.model_validate(dict(form))
        for key, value in data.model_dump().items():
            setattr(c, key, value)
        db.add(AuditLog(actor_id=user.telegram_id, action="contest:edited", entity_id=c.id))
        db.commit()
    except (ValidationError, DomainError, IntegrityError) as exc:
        db.rollback()
        errors = (
            form_errors(exc)
            if isinstance(exc, ValidationError)
            else [exc.message if isinstance(exc, DomainError) else "Этот slug уже используется."]
        )
        return render(
            request,
            "admin/contest_form.html",
            user,
            status_code=422,
            values=dict(form),
            errors=errors,
            editing=True,
            tab="contests",
        )
    return redirect(request, f"/admin/contests/{contest_id}", "Конкурс сохранён.")


@router.post("/contests/{contest_id}/action")
def contest_action(
    request: Request, contest_id: int, user=Depends(require_admin), form=Depends(safe_form), db=Depends(get_db)
):
    action = str(form.get("action", ""))
    if action == "results":
        publish_results(db, contest_id, user.telegram_id)
    else:
        change_state(db, contest_id, action, user.telegram_id, str(form.get("reason", "")))
    db.commit()
    return redirect(request, f"/admin/contests/{contest_id}", "Изменения сохранены.")


@router.get("/payments")
def payments(request: Request, page: int = 1, state: str = "pending", user=Depends(require_admin), db=Depends(get_db)):
    state = state if state in {"pending", "approved", "rejected", "all"} else "pending"
    page = max(1, page)
    query = select(Payment).order_by(Payment.created_at.desc())
    if state != "all":
        query = query.where(Payment.status == state)
    items = list(db.scalars(query.offset((page - 1) * 20).limit(21)))
    return render(
        request,
        "admin/list.html",
        user,
        tab="payments",
        items=items[:20],
        page=page,
        state=state,
        has_next=len(items) > 20,
    )


@router.post("/payments/{payment_id}/review")
def payment_review(
    request: Request, payment_id: int, user=Depends(require_admin), form=Depends(safe_form), db=Depends(get_db)
):
    try:
        version = int(form.get("version", "0"))
    except ValueError as exc:
        raise DomainError("Обновите страницу платежей.") from exc
    action = form.get("action")
    if action not in {"approve", "reject"}:
        raise DomainError("Неизвестное действие.")
    review_payment(db, payment_id, version, user.telegram_id, action == "approve", str(form.get("reason", "")))
    db.commit()
    return redirect(request, "/admin/payments", "Платёж обработан.")


@router.get("/submissions")
def submissions(request: Request, page: int = 1, user=Depends(require_admin), db=Depends(get_db)):
    page = max(1, page)
    items = list(
        db.scalars(select(Submission).order_by(Submission.submitted_at.desc()).offset((page - 1) * 20).limit(21))
    )
    return render(
        request, "admin/list.html", user, tab="submissions", items=items[:20], page=page, has_next=len(items) > 20
    )


@router.get("/submissions/{submission_id}")
def score_page(request: Request, submission_id: int, user=Depends(require_admin), db=Depends(get_db)):
    sub = db.get(Submission, submission_id)
    if not sub:
        raise DomainError("Работа не найдена.", 404)
    c = locked_contest(db, sub.participation.contest_id)
    db.commit()
    score = next((s for s in sub.scores if s.judge_telegram_id == user.telegram_id), None)
    return render(request, "admin/score.html", user, sub=sub, c=c, values=score or {}, tab="submissions")


@router.post("/submissions/{submission_id}/score")
def score_post(
    request: Request, submission_id: int, user=Depends(require_admin), form=Depends(safe_form), db=Depends(get_db)
):
    sub = db.get(Submission, submission_id)
    if not sub:
        raise DomainError("Работа не найдена.", 404)
    try:
        data = ScoreForm.model_validate(dict(form))
        save_score(db, submission_id, user.telegram_id, data)
        db.commit()
    except (ValidationError, DomainError) as exc:
        db.rollback()
        errors = form_errors(exc) if isinstance(exc, ValidationError) else [exc.message]
        return render(
            request,
            "admin/score.html",
            user,
            status_code=422,
            errors=errors,
            sub=sub,
            c=sub.participation.contest,
            values=dict(form),
            tab="submissions",
        )
    return redirect(request, f"/admin/submissions/{submission_id}", "Оценка сохранена.")


@router.get("/activity")
def activity(request: Request, page: int = 1, user=Depends(require_admin), db=Depends(get_db)):
    page = max(1, page)
    items = list(db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).offset((page - 1) * 30).limit(31)))
    failed = list(
        db.scalars(
            select(Notification).where(Notification.status == "failed").order_by(Notification.id.desc()).limit(50)
        )
    )
    pending = db.scalar(select(func.count(Notification.id)).where(Notification.status.in_(["pending", "sending"])))
    return render(
        request,
        "admin/activity.html",
        user,
        items=items[:30],
        page=page,
        has_next=len(items) > 30,
        failed=failed,
        pending=pending,
        tab="activity",
    )


@router.post("/notifications/{notification_id}/retry")
def retry_notification(
    request: Request, notification_id: int, user=Depends(require_admin), form=Depends(safe_form), db=Depends(get_db)
):
    n = db.scalar(select(Notification).where(Notification.id == notification_id).with_for_update())
    if not n or n.status != "failed":
        raise DomainError("Уведомление не найдено или уже в очереди.", 409)
    n.status, n.attempts, n.available_at, n.last_error = "pending", 0, now(), None
    db.add(AuditLog(actor_id=user.telegram_id, action="notification:retry", entity_id=n.id))
    db.commit()
    return redirect(request, "/admin/activity", "Уведомление вернулось в очередь.")
