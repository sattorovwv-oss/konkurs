from sqlalchemy import select, func
from app.models import Participation, Payment, AuditLog
from app.services.contest_state import locked_contest, registration_open
from app.services.errors import DomainError
from app.services.telegram_notifications import enqueue
from app.config import get_settings
from app.utils.time import now


def count_approved(db, contest_id):
    return (
        db.scalar(
            select(func.count(Participation.id)).where(
                Participation.contest_id == contest_id, Participation.status == "approved"
            )
        )
        or 0
    )


def join_contest(db, contest_id, user):
    c = locked_contest(db, contest_id)
    existing = db.scalar(
        select(Participation).where(Participation.contest_id == c.id, Participation.user_id == user.id)
    )
    if existing:
        return existing
    if not registration_open(c):
        raise DomainError("Регистрация уже закрыта или конкурс приостановлен.", 409)
    if count_approved(db, c.id) >= c.max_participants:
        raise DomainError("Все места уже заняты.", 409)
    p = Participation(contest_id=c.id, user_id=user.id)
    db.add(p)
    db.flush()
    if c.fee_tjs == 0:
        approve_participation(db, c, p)
    return p


def approve_participation(db, c, p):
    if count_approved(db, c.id) >= c.max_participants:
        raise DomainError("Все места уже заняты. Свяжитесь с организатором по поводу возврата.", 409)
    p.status, p.participant_no, p.approved_at = "approved", c.next_participant_no, now()
    c.next_participant_no += 1
    db.flush()
    enqueue(
        db,
        f"approved:{p.id}",
        p.user.telegram_id,
        f"✅ Участие подтверждено.\n{c.title}\nВаш номер: #{p.participant_no}\n{get_settings().app_url}/contests/{c.slug}",
    )


def create_payment(db, participation_id, user, stored):
    p = db.get(Participation, participation_id)
    if not p or p.user_id != user.id:
        raise DomainError("Участие не найдено.", 404)
    c = locked_contest(db, p.contest_id)
    db.refresh(p)
    if not registration_open(c):
        raise DomainError("Приём чеков закрыт или конкурс приостановлен.", 409)
    if p.status in {"approved", "payment_pending"}:
        raise DomainError("Чек уже на проверке или оплата подтверждена.", 409)
    if count_approved(db, c.id) >= c.max_participants:
        raise DomainError("Все места заняты. Если вы уже оплатили, обратитесь к организатору.", 409)
    pay = db.scalar(select(Payment).where(Payment.participation_id == p.id))
    old = pay.receipt_path if pay else None
    if pay:
        pay.version += 1
        pay.status, pay.reject_reason, pay.reviewed_by, pay.reviewed_at = "pending", None, None, None
    else:
        pay = Payment(participation_id=p.id, amount_tjs=c.fee_tjs, version=1)
        db.add(pay)
    pay.receipt_path, pay.original_name, pay.mime_type = stored.key, stored.name, stored.mime
    p.status = "payment_pending"
    db.flush()
    s = get_settings()
    if not s.admin_chat_id:
        raise DomainError("Организатор ещё не настроил приём платежей.", 503)
    enqueue(
        db,
        f"payment:{pay.id}:{pay.version}",
        s.admin_chat_id,
        f"💳 Новый платёж\nКонкурс: {c.title}\nПользователь: {user.display_name}\nTelegram ID: {user.telegram_id}\nСумма: {pay.amount_tjs} TJS\nЗаявка: #{p.id}\nНомер участника будет назначен после одобрения.",
        payment_id=pay.id,
        version=pay.version,
    )
    return pay, old


def review_payment(db, payment_id, version, actor_id, approve, reason=""):
    if actor_id not in get_settings().admin_ids:
        raise DomainError("Только администратор может проверить платёж.", 403)
    pay = db.get(Payment, payment_id)
    if not pay:
        raise DomainError("Платёж не найден.", 404)
    c = locked_contest(db, pay.participation.contest_id)
    db.refresh(pay)
    p = db.get(Participation, pay.participation_id)
    db.refresh(p)
    if pay.status != "pending" or pay.version != version:
        raise DomainError("Этот чек уже обработан или заменён. Обновите страницу.", 409)
    if approve:
        if c.status not in {"scheduled", "running"}:
            raise DomainError("В этом состоянии конкурса подтверждать оплату нельзя.", 409)
        approve_participation(db, c, p)
        pay.status = "approved"
    else:
        if not reason.strip():
            raise DomainError("Укажите причину отклонения.")
        pay.status, pay.reject_reason, p.status = "rejected", reason.strip()[:2000], "rejected"
        enqueue(
            db,
            f"rejected:{pay.id}:{pay.version}",
            p.user.telegram_id,
            f"❌ Чек отклонён: {pay.reject_reason}\nКонкурс: {c.title}\nВы можете отправить новый чек до закрытия регистрации.\n{get_settings().app_url}/payments/{p.id}",
        )
    pay.reviewed_by, pay.reviewed_at = actor_id, now()
    db.add(
        AuditLog(
            actor_id=actor_id,
            action="payment:" + pay.status,
            entity_id=pay.id,
            details={"version": pay.version, "reason": reason[:2000]},
        )
    )
    db.flush()
    return pay
