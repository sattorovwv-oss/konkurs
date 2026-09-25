import asyncio
import logging
from datetime import timedelta
from sqlalchemy import select, or_, and_
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest, TelegramRetryAfter
from aiogram.types import BufferedInputFile
from starlette.concurrency import run_in_threadpool
from app.db import SessionLocal
from app.models import Notification, Payment
from app.services.storage import Storage
from app.utils.time import now
from bot.keyboards.payments import review_keyboard

logger = logging.getLogger(__name__)


def claim():
    with SessionLocal() as db:
        n = db.scalar(
            select(Notification)
            .where(
                or_(
                    and_(Notification.status == "pending", Notification.available_at <= now()),
                    and_(Notification.status == "sending", Notification.lease_until < now()),
                )
            )
            .order_by(Notification.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not n:
            return None
        n.status, n.lease_until = "sending", now() + timedelta(minutes=3)
        n.attempts += 1
        db.commit()
        return n


def resolve_receipt(payment_id, version):
    with SessionLocal() as db:
        pay = db.get(Payment, payment_id)
        if not pay or pay.version != version or pay.status != "pending":
            return None
        return Storage().read(pay.receipt_path), pay.original_name


def complete(notification_id, attempt, error=None, permanent=False, retry_after=None):
    with SessionLocal() as db:
        n = db.scalar(select(Notification).where(Notification.id == notification_id).with_for_update())
        if not n or n.attempts != attempt or n.status != "sending":
            return
        n.lease_until = None
        if error is None:
            n.status, n.sent_at, n.last_error = "sent", now(), None
        else:
            n.status = "failed" if permanent or n.attempts >= 12 else "pending"
            n.last_error = error[:100]  # Exception class only, never API URL/token.
            n.available_at = now() + timedelta(seconds=retry_after or min(3600, 2**n.attempts * 5))
        db.commit()


async def deliver(bot, n):
    p = n.payload
    if "payment_id" in p:
        receipt = await run_in_threadpool(resolve_receipt, p["payment_id"], p["version"])
        if receipt:
            data, name = receipt
            await bot.send_document(
                n.chat_id,
                BufferedInputFile(data, filename=name),
                caption=p["text"][:1000],
                reply_markup=review_keyboard(p["payment_id"], p["version"]),
            )
    else:
        await bot.send_message(n.chat_id, p["text"], disable_web_page_preview=True)


async def worker(bot, stop):
    while not stop.is_set():
        try:
            n = await run_in_threadpool(claim)
            if not n:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=2)
                except TimeoutError:
                    continue
                continue
            try:
                await deliver(bot, n)
            except TelegramRetryAfter as exc:
                await run_in_threadpool(complete, n.id, n.attempts, "TelegramRetryAfter", False, exc.retry_after + 1)
            except (TelegramForbiddenError, TelegramBadRequest) as exc:
                await run_in_threadpool(complete, n.id, n.attempts, type(exc).__name__, True)
            except Exception as exc:
                await run_in_threadpool(complete, n.id, n.attempts, type(exc).__name__)
            else:
                await run_in_threadpool(complete, n.id, n.attempts)
            await asyncio.sleep(0.08)
        except Exception as exc:
            logger.error("Notification worker error: %s", type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except TimeoutError:
                continue
