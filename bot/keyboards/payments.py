from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

REASONS = {"amount": "Неверная сумма", "unreadable": "Чек нечитаемый", "missing": "Платёж не найден", "other": "Другое"}


def review_keyboard(payment_id, version):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Одобрить", callback_data=f"pay:approve:{payment_id}:{version}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"pay:reject:{payment_id}:{version}"),
            ]
        ]
    )


def reasons_keyboard(payment_id, version):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"reason:{key}:{payment_id}:{version}")]
            for key, label in REASONS.items()
        ]
        + [[InlineKeyboardButton(text="Назад", callback_data=f"pay:back:{payment_id}:{version}")]]
    )
