from datetime import timedelta
import pytest
from sqlalchemy import select, func
from app.models import Notification, User, Participation
from app.services.contest_state import advance, change_state, submission_open
from app.services.errors import DomainError
from app.services.participation import join_contest, count_approved
from app.utils.time import now, aware
from conftest import make_contest


def test_auto_start_and_finish(db):
    c = make_contest(db)
    starts, ends = aware(c.starts_at), aware(c.ends_at)
    advance(db, c, starts)
    assert c.status == "running" and c.actual_started_at == c.starts_at
    advance(db, c, ends)
    assert c.status == "finished" and c.finished_at == c.ends_at
    assert not submission_open(c, ends)


def test_downtime_does_not_send_late_start(db):
    c = make_contest(db)
    advance(db, c, aware(c.ends_at) + timedelta(seconds=1))
    assert c.status == "finished"
    assert db.scalar(select(func.count(Notification.id))) == 0


def test_frozen_scheduled_shifts_dates(db):
    c = make_contest(db)
    starts, ends, registration = aware(c.starts_at), aware(c.ends_at), aware(c.registration_deadline)
    change_state(db, c.id, "freeze", 900001, "Host maintenance")
    c.frozen_at = now() - timedelta(minutes=30)
    advance(db, c, aware(c.ends_at) + timedelta(days=1))
    assert c.status == "frozen"
    change_state(db, c.id, "resume", 900001)
    assert c.status == "scheduled"
    assert abs((aware(c.starts_at) - starts).total_seconds() - 1800) < 2
    assert abs((aware(c.ends_at) - ends).total_seconds() - 1800) < 2
    assert abs((aware(c.registration_deadline) - registration).total_seconds() - 1800) < 2


def test_running_freeze_only_shifts_deadline(db):
    c = make_contest(db)
    change_state(db, c.id, "start", 900001)
    starts = aware(c.starts_at)
    change_state(db, c.id, "freeze", 900001, "Network issue")
    c.frozen_at = now() - timedelta(minutes=20)
    db.flush()
    change_state(db, c.id, "resume", 900001)
    assert c.status == "running" and aware(c.starts_at) == starts


def test_cancel_and_reason_validation(db):
    c = make_contest(db)
    for action in ["freeze", "cancel"]:
        with pytest.raises(DomainError):
            change_state(db, c.id, action, 900001, "")
    change_state(db, c.id, "cancel", 900001, "Organizer unavailable")
    assert c.status == "cancelled"
    with pytest.raises(DomainError):
        change_state(db, c.id, "start", 900001)


def test_unique_participation_and_free_capacity(db):
    c = make_contest(db, fee_tjs=0, max_participants=1)
    a = User(telegram_id=111, first_name="A")
    b = User(telegram_id=222, first_name="B")
    db.add_all([a, b])
    db.flush()
    first = join_contest(db, c.id, a)
    second = join_contest(db, c.id, a)
    assert first.id == second.id and first.participant_no == 1
    with pytest.raises(DomainError):
        join_contest(db, c.id, b)
    assert count_approved(db, c.id) == 1
    assert db.scalar(select(func.count(Participation.id))) == 1
