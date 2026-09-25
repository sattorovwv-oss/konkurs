from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Contest, Participation, AuditLog
from app.services.errors import DomainError
from app.services.telegram_notifications import enqueue
from app.config import get_settings
from app.utils.time import now, aware


def notify_start(db, contest):
    for p in db.scalars(
        select(Participation).where(Participation.contest_id == contest.id, Participation.status == "approved")
    ):
        enqueue(
            db,
            f"start:{contest.id}:{p.id}",
            p.user.telegram_id,
            f"🚀 Конкурс начался!\n{contest.title}\nТехническое задание доступно:\n{get_settings().app_url}/contests/{contest.slug}",
        )


def advance(db: Session, contest: Contest, at=None):
    at = at or now()
    if contest.status == "scheduled" and contest.published and at >= aware(contest.starts_at):
        # After downtime an expired contest must not send a misleading start notification.
        if at >= aware(contest.ends_at):
            contest.status = "finished"
            contest.actual_started_at = contest.starts_at
            contest.finished_at = contest.ends_at
        else:
            contest.status = "running"
            contest.actual_started_at = contest.starts_at
            notify_start(db, contest)
    if contest.status == "running" and at >= aware(contest.ends_at):
        contest.status = "finished"
        contest.finished_at = contest.ends_at
    db.flush()
    return contest


def locked_contest(db, contest_id):
    contest = db.scalar(
        select(Contest)
        .where(Contest.id == contest_id)
        .with_for_update(of=Contest)
        .execution_options(populate_existing=True)
    )
    if not contest:
        raise DomainError("Конкурс не найден.", 404)
    return advance(db, contest)


def registration_open(contest, at=None):
    return contest.published and contest.status == "scheduled" and (at or now()) < aware(contest.registration_deadline)


def submission_open(contest, at=None):
    return contest.status == "running" and (at or now()) < aware(contest.ends_at)


def change_state(db, contest_id, action, actor_id, reason=""):
    c = locked_contest(db, contest_id)
    stamp = now()
    reason = reason.strip()
    if c.results_published:
        raise DomainError("Результаты уже опубликованы. Конкурс закрыт для изменений.", 409)
    if action == "publish":
        if c.status != "draft" or aware(c.registration_deadline) <= stamp:
            raise DomainError("Опубликовать можно черновик с будущей датой регистрации.", 409)
        c.published, c.status = True, "scheduled"
    elif action == "start":
        if c.status != "scheduled" or stamp >= aware(c.ends_at):
            raise DomainError("Сейчас этот конкурс нельзя запустить.", 409)
        c.status, c.actual_started_at = "running", stamp
        notify_start(db, c)
    elif action == "freeze":
        if c.status not in {"scheduled", "running"} or not reason:
            raise DomainError("Для заморозки активного конкурса укажите причину.", 409)
        c.previous_status, c.frozen_at = c.status, stamp
        c.status, c.freeze_reason = "frozen", reason[:2000]
    elif action == "resume":
        if c.status != "frozen":
            raise DomainError("Конкурс не заморожен.", 409)
        pause = stamp - aware(c.frozen_at)
        # Freeze pauses all clocks if scheduled, only submission deadline if already running.
        if c.previous_status == "scheduled":
            c.registration_deadline += pause
            c.starts_at += pause
        c.ends_at += pause
        c.status = c.previous_status
        c.frozen_at = c.previous_status = c.freeze_reason = None
        advance(db, c, stamp)
    elif action == "finish":
        if c.status not in {"running", "frozen"} or c.actual_started_at is None:
            raise DomainError("Завершить можно уже начавшийся конкурс.", 409)
        c.status, c.finished_at = "finished", stamp
    elif action == "cancel":
        if c.status in {"cancelled", "finished"} or not reason:
            raise DomainError("Укажите причину. Завершённый конкурс отменить нельзя.", 409)
        c.status, c.cancel_reason = "cancelled", reason[:2000]
        for p in db.scalars(
            select(Participation).where(
                Participation.contest_id == c.id, Participation.status.in_(["approved", "payment_pending"])
            )
        ):
            enqueue(
                db,
                f"cancel:{c.id}:{p.id}",
                p.user.telegram_id,
                f"❌ Конкурс «{c.title}» отменён.\nПричина: {c.cancel_reason}\nЕсли вы оплатили участие, обратитесь к организатору по вопросу возврата.\n{get_settings().prize_contact_url}",
            )
    else:
        raise DomainError("Неизвестное действие.", 400)
    db.add(AuditLog(actor_id=actor_id, action="contest:" + action, entity_id=c.id, details={"reason": reason}))
    db.flush()
    return c


def tick(db):
    ids = list(db.scalars(select(Contest.id).where(Contest.status.in_(["scheduled", "running"]))))
    for contest_id in ids:
        locked_contest(db, contest_id)
        db.commit()
