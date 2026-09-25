from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select
import pytest
from app.db import engine, SessionLocal
from app.models import User, Participation, Payment
from app.services.participation import join_contest, review_payment
from app.services.errors import DomainError
from conftest import make_contest

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(engine.dialect.name != "postgresql", reason="requires PostgreSQL TEST_DATABASE_URL"),
]


def pending(db, c, tg_id):
    user = User(telegram_id=tg_id, first_name=f"User {tg_id}")
    db.add(user)
    db.commit()
    p = join_contest(db, c.id, user)
    p.status = "payment_pending"
    pay = Payment(
        participation_id=p.id,
        amount_tjs=50,
        receipt_path="receipts/" + "a" * 32 + ".png",
        original_name="r.png",
        mime_type="image/png",
        status="pending",
    )
    db.add(pay)
    db.commit()
    return pay.id


def approve(pid):
    with SessionLocal() as session:
        try:
            review_payment(session, pid, 1, 900001, True)
            session.commit()
            return "approved"
        except DomainError:
            session.rollback()
            return "blocked"


def test_concurrent_approvals_one_remaining_slot(db):
    c = make_contest(db, max_participants=1)
    ids = [pending(db, c, 771), pending(db, c, 772)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(approve, ids))
    assert sorted(results) == ["approved", "blocked"]
    db.expire_all()
    approved = list(db.scalars(select(Participation).where(Participation.status == "approved")))
    assert len(approved) == 1 and approved[0].participant_no == 1


def test_concurrent_repeat_approval_is_idempotent(db):
    c = make_contest(db)
    pid = pending(db, c, 781)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(approve, [pid, pid]))
    assert sorted(results) == ["approved", "blocked"]
