from datetime import timedelta
from app.models import Notification
from app.services.telegram_notifications import enqueue
from app.utils.time import now
from bot.worker import claim, complete, deliver
from bot.handlers.admin import allowed, review
from app.services.errors import DomainError
import pytest


class RecordingBot:
    def __init__(self):
        self.messages = []
        self.documents = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text))

    async def send_document(self, chat_id, document, **kwargs):
        self.documents.append((chat_id, document, kwargs))


async def test_outbox_delivery_and_failure_isolation(db):
    enqueue(db, "first", 101, "Hello first")
    enqueue(db, "second", 102, "Hello second")
    db.commit()
    bot = RecordingBot()
    first = claim()
    complete(first.id, first.attempts, "NetworkError")
    second = claim()
    await deliver(bot, second)
    complete(second.id, second.attempts)
    db.expire_all()
    assert bot.messages == [(102, "Hello second")]
    assert db.get(Notification, first.id).status == "pending"
    assert db.get(Notification, second.id).status == "sent"
    assert claim() is None
    n = db.get(Notification, first.id)
    n.available_at = now() - timedelta(seconds=1)
    db.commit()
    retried = claim()
    await deliver(bot, retried)
    complete(retried.id, retried.attempts)
    assert bot.messages[-1] == (101, "Hello first")


def test_expired_lease_and_stale_worker_cannot_complete(db):
    enqueue(db, "lease", 101, "Hello")
    db.commit()
    first = claim()
    n = db.get(Notification, first.id)
    n.lease_until = now() - timedelta(seconds=1)
    db.commit()
    second = claim()
    assert second.attempts == 2
    complete(first.id, first.attempts)
    db.expire_all()
    assert db.get(Notification, first.id).status == "sending"
    complete(second.id, second.attempts)
    db.expire_all()
    assert db.get(Notification, first.id).status == "sent"


def test_bot_admin_authorization():
    assert allowed(900001)
    assert not allowed(100001)
    with pytest.raises(DomainError) as caught:
        review(1, 1, 100001, True)
    assert caught.value.status == 403


async def test_admin_receipt_document_and_keyboard(db, member_client):
    from conftest import make_contest, png_bytes
    from app.services.storage import Storage
    from app.services.participation import join_contest, create_payment

    member, user, _ = member_client
    c = make_contest(db)
    participation = join_contest(db, c.id, user)
    stored = Storage().save(png_bytes(), "payment.png", "image/png", "receipts")
    pay, _ = create_payment(db, participation.id, user, stored)
    db.commit()
    bot = RecordingBot()
    n = claim()
    await deliver(bot, n)
    assert len(bot.documents) == 1
    chat, document, args = bot.documents[0]
    assert chat == -900001 and document.data == png_bytes()
    assert f"pay:approve:{pay.id}:1" == args["reply_markup"].inline_keyboard[0][0].callback_data
    assert user.display_name in args["caption"]
