from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ForceReply
from starlette.concurrency import run_in_threadpool
from app.config import get_settings
from app.db import SessionLocal
from app.services.errors import DomainError
from app.services.participation import review_payment
from bot.keyboards.payments import REASONS, review_keyboard, reasons_keyboard

router = Router()


class Rejection(StatesGroup):
    reason = State()


def allowed(telegram_id):
    return telegram_id in get_settings().admin_ids


def review(payment_id, version, actor, approve, reason=""):
    with SessionLocal() as db:
        review_payment(db, payment_id, version, actor, approve, reason)
        db.commit()


@router.message(Command("start", "help"))
async def help_command(message: Message):
    s = get_settings()
    text = f"💚 NEXVIRO Code Battle\nКонкурсы и ваш профиль: {s.app_url}\nЗдесь вы будете получать уведомления о статусе участия, старте и результатах."
    if allowed(message.from_user.id):
        text += "\n\nAdmin: проверяйте чеки кнопками под файлами. «Другое» позволяет написать свою причину. /cancel — отменить ввод причины."
    await message.answer(text)


@router.message(Command("cancel"))
async def cancel_reason(message: Message, state: FSMContext):
    if allowed(message.from_user.id):
        await state.clear()
        await message.answer("Ввод причины отменён.")


@router.callback_query(F.data.startswith("pay:"))
async def payment_callback(query: CallbackQuery, state: FSMContext):
    if not allowed(query.from_user.id):
        return await query.answer("Доступ запрещён.", show_alert=True)
    parts = query.data.split(":")
    if len(parts) != 4 or not parts[2].isdigit() or not parts[3].isdigit():
        return await query.answer("Некорректная кнопка.", show_alert=True)
    _, action, payment_id, version = parts
    payment_id, version = int(payment_id), int(version)
    await state.clear()
    if action == "reject":
        await query.message.edit_reply_markup(reply_markup=reasons_keyboard(payment_id, version))
        return await query.answer("Выберите причину")
    if action == "back":
        await query.message.edit_reply_markup(reply_markup=review_keyboard(payment_id, version))
        return await query.answer()
    if action != "approve":
        return await query.answer("Неизвестное действие.", show_alert=True)
    try:
        await run_in_threadpool(review, payment_id, version, query.from_user.id, True)
    except DomainError as exc:
        return await query.answer(exc.message[:190], show_alert=True)
    await query.answer("Оплата подтверждена")
    await query.message.edit_reply_markup(reply_markup=None)
    await query.message.reply(f"✅ Платёж #{payment_id} подтверждён администратором {query.from_user.id}.")


@router.callback_query(F.data.startswith("reason:"))
async def rejection_callback(query: CallbackQuery, state: FSMContext):
    if not allowed(query.from_user.id):
        return await query.answer("Доступ запрещён.", show_alert=True)
    parts = query.data.split(":")
    if len(parts) != 4 or parts[1] not in REASONS or not parts[2].isdigit() or not parts[3].isdigit():
        return await query.answer("Некорректная кнопка.", show_alert=True)
    _, key, payment_id, version = parts
    if key == "other":
        await state.set_state(Rejection.reason)
        prompt = await query.message.reply(
            "Напишите причину отклонения ответом на это сообщение. /cancel — отмена.",
            reply_markup=ForceReply(selective=True),
        )
        await state.update_data(
            payment_id=int(payment_id),
            version=int(version),
            message_id=query.message.message_id,
            prompt_id=prompt.message_id,
        )
        return await query.answer()
    try:
        await run_in_threadpool(review, int(payment_id), int(version), query.from_user.id, False, REASONS[key])
    except DomainError as exc:
        return await query.answer(exc.message[:190], show_alert=True)
    await query.answer("Чек отклонён")
    await query.message.edit_reply_markup(reply_markup=None)
    await query.message.reply(f"❌ Чек #{payment_id} отклонён: {REASONS[key]}")


@router.message(Rejection.reason, F.text)
async def custom_reason(message: Message, state: FSMContext):
    if not allowed(message.from_user.id):
        return
    data = await state.get_data()
    if not message.reply_to_message or message.reply_to_message.message_id != data.get("prompt_id"):
        return await message.answer("Ответьте на сообщение с просьбой указать причину.")
    if not 3 <= len(message.text.strip()) <= 2000:
        return await message.answer("Причина должна содержать от 3 до 2000 символов.")
    try:
        await run_in_threadpool(review, data["payment_id"], data["version"], message.from_user.id, False, message.text)
    except DomainError as exc:
        await state.clear()
        return await message.answer(exc.message)
    await state.clear()
    await message.answer(f"❌ Чек #{data['payment_id']} отклонён. Участник получит уведомление.")
    await message.bot.edit_message_reply_markup(
        chat_id=message.chat.id, message_id=data["message_id"], reply_markup=None
    )
