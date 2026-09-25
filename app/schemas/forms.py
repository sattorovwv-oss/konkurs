import re
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field, field_validator, model_validator
from app.utils.time import from_local

Money = Decimal


class ContestForm(BaseModel):
    title: str = Field(min_length=3, max_length=180)
    slug: str = Field(min_length=3, max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    summary: str = Field(min_length=10, max_length=1000)
    full_task: str = Field(min_length=10, max_length=50000)
    rules: str = Field(min_length=5, max_length=20000)
    max_participants: int = Field(ge=1, le=10000)
    fee_tjs: Money = Field(ge=0, le=1000000, decimal_places=2)
    prize_first_tjs: Money = Field(ge=0, le=1000000, decimal_places=2)
    prize_second_tjs: Money = Field(ge=0, le=1000000, decimal_places=2)
    prize_third_tjs: Money = Field(ge=0, le=1000000, decimal_places=2)
    registration_deadline: datetime
    starts_at: datetime
    ends_at: datetime

    @field_validator("registration_deadline", "starts_at", "ends_at", mode="before")
    @classmethod
    def dates(cls, value):
        return from_local(value)

    @model_validator(mode="after")
    def chronological(self):
        if self.registration_deadline >= self.starts_at:
            raise ValueError("Регистрация должна закрываться раньше старта.")
        if self.starts_at >= self.ends_at:
            raise ValueError("Дедлайн должен быть позже старта.")
        return self


class ScoreForm(BaseModel):
    functionality: int = Field(ge=0, le=40)
    ui: int = Field(ge=0, le=20)
    code_quality: int = Field(ge=0, le=20)
    stability: int = Field(ge=0, le=10)
    originality: int = Field(ge=0, le=10)
    comment: str = Field(default="", max_length=5000)


class SubmissionForm(BaseModel):
    bot_username: str
    description: str = Field(min_length=10, max_length=10000)
    github_url: str | None = Field(default=None, max_length=300)

    @field_validator("bot_username")
    @classmethod
    def username(cls, value):
        value = value.strip().lstrip("@")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", value) or not value.lower().endswith("bot"):
            raise ValueError("Укажите username бота: от 5 до 32 символов, заканчивается на bot.")
        return value

    @field_validator("github_url", mode="before")
    @classmethod
    def github(cls, value):
        if not value or not value.strip():
            return None
        if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?", value.strip()):
            raise ValueError("GitHub: укажите https://github.com/владелец/репозиторий или оставьте пустым.")
        return value.strip()


def form_errors(exc) -> list[str]:
    messages = []
    labels = {
        "title": "Название",
        "slug": "Slug",
        "summary": "Описание",
        "full_task": "ТЗ",
        "rules": "Правила",
        "max_participants": "Количество участников",
        "description": "Описание проекта",
    }
    for error in exc.errors():
        message = error["msg"].removeprefix("Value error, ")
        if error["type"] != "value_error":
            field = str(error["loc"][0]) if error["loc"] else "Форма"
            message = f"{labels.get(field, field)}: проверьте значение, длину и допустимый диапазон."
        messages.append(message)
    return messages
