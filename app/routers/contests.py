from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, func
from app.db import get_db
from app.models import Contest, Participation, Payment, Submission
from app.services.contest_state import locked_contest, registration_open, submission_open, tick
from app.services.errors import DomainError
from app.services.participation import join_contest, count_approved
from app.services.ranking import ranking
from app.utils.security import current_user, require_user, is_admin, safe_form
from app.utils.web import render, redirect
from app.utils.time import now

router = APIRouter()


def visible_contest(db, slug, user):
    c = db.scalar(select(Contest).where(Contest.slug == slug))
    if not c or (not c.published and not is_admin(user)):
        raise DomainError("Конкурс не найден.", 404)
    c = locked_contest(db, c.id)
    db.commit()
    return c


@router.get("/")
@router.get("/contests")
def home(request: Request, page: int = 1, user=Depends(current_user), db=Depends(get_db)):
    tick(db)
    page = max(1, page)
    contests = list(
        db.scalars(
            select(Contest)
            .where(Contest.published.is_(True))
            .order_by(Contest.created_at.desc())
            .offset((page - 1) * 12)
            .limit(13)
        )
    )
    counts = dict(
        db.execute(
            select(Participation.contest_id, func.count())
            .where(Participation.status == "approved")
            .group_by(Participation.contest_id)
        ).all()
    )
    return render(
        request, "home.html", user, contests=contests[:12], counts=counts, page=page, has_next=len(contests) > 12
    )


@router.get("/contests/{slug}")
def detail(request: Request, slug: str, user=Depends(current_user), db=Depends(get_db)):
    c = visible_contest(db, slug, user)
    p = (
        db.scalar(select(Participation).where(Participation.contest_id == c.id, Participation.user_id == user.id))
        if user
        else None
    )
    sub = db.scalar(select(Submission).where(Submission.participation_id == p.id)) if p else None
    pay = db.scalar(select(Payment).where(Payment.participation_id == p.id)) if p else None
    rows = ranking(db, c) if c.results_published else []
    mine = next((r for r in rows if p and r.participant.id == p.id), None)
    task_visible = is_admin(user) or bool(
        p and p.status == "approved" and c.actual_started_at is not None and c.status != "cancelled"
    )
    return render(
        request,
        "contest.html",
        user,
        c=c,
        p=p,
        sub=sub,
        pay=pay,
        rows=rows,
        mine=mine,
        task_visible=task_visible,
        count=count_approved(db, c.id),
        can_join=registration_open(c),
        can_submit=bool(p and p.status == "approved" and submission_open(c)),
        stamp=now(),
    )


@router.post("/contests/{slug}/join")
def join(request: Request, slug: str, user=Depends(require_user), form=Depends(safe_form), db=Depends(get_db)):
    c = visible_contest(db, slug, user)
    p = join_contest(db, c.id, user)
    db.commit()
    url = f"/contests/{slug}" if p.status == "approved" else f"/payments/{p.id}"
    return redirect(
        request,
        url,
        "Участие подтверждено."
        if p.status == "approved"
        else "Заявка создана. Следующий шаг — оплата и загрузка чека.",
    )


@router.get("/contests/{slug}/status")
def status(slug: str, user=Depends(current_user), db=Depends(get_db)):
    c = visible_contest(db, slug, user)
    p = (
        db.scalar(select(Participation).where(Participation.contest_id == c.id, Participation.user_id == user.id))
        if user
        else None
    )
    return {
        "status": c.status,
        "count": count_approved(db, c.id),
        "results": c.results_published,
        "participation": p.status if p else None,
        "number": p.participant_no if p else None,
        "starts_at": c.starts_at.isoformat(),
        "ends_at": c.ends_at.isoformat(),
    }


@router.get("/profile")
def profile(request: Request, user=Depends(require_user), db=Depends(get_db)):
    tick(db)
    participations = list(
        db.scalars(
            select(Participation).where(Participation.user_id == user.id).order_by(Participation.created_at.desc())
        )
    )
    results = {}
    for p in participations:
        if p.contest.results_published:
            results[p.id] = next((r for r in ranking(db, p.contest) if r.participant.id == p.id), None)
    return render(request, "profile.html", user, participations=participations, results=results)
