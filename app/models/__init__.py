from datetime import datetime
from decimal import Decimal
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db import Base
from app.utils.time import now


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class User(Timestamps, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    oidc_subject: Mapped[str | None] = mapped_column(String(255), unique=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))
    photo_url: Mapped[str | None] = mapped_column(Text)

    @property
    def display_name(self):
        return "@" + self.username if self.username else self.first_name


class Contest(Timestamps, Base):
    __tablename__ = "contests"
    __table_args__ = (
        CheckConstraint("registration_deadline < starts_at AND starts_at < ends_at", name="contest_dates"),
        CheckConstraint("max_participants > 0", name="positive_capacity"),
        CheckConstraint(
            "fee_tjs >= 0 AND prize_first_tjs >= 0 AND prize_second_tjs >= 0 AND prize_third_tjs >= 0",
            name="nonnegative_money",
        ),
        CheckConstraint(
            "status IN ('draft','scheduled','running','frozen','finished','cancelled')", name="contest_status"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(180))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    summary: Mapped[str] = mapped_column(String(1000))
    full_task: Mapped[str] = mapped_column(Text)
    rules: Mapped[str] = mapped_column(Text)
    max_participants: Mapped[int] = mapped_column(Integer)
    fee_tjs: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    prize_first_tjs: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    prize_second_tjs: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    prize_third_tjs: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    registration_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    freeze_reason: Mapped[str | None] = mapped_column(Text)
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    previous_status: Mapped[str | None] = mapped_column(String(20))
    actual_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    results_published: Mapped[bool] = mapped_column(Boolean, default=False)
    results_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_participant_no: Mapped[int] = mapped_column(Integer, default=1)

    @property
    def prize_pool(self):
        return self.prize_first_tjs + self.prize_second_tjs + self.prize_third_tjs


class Participation(Base):
    __tablename__ = "participations"
    __table_args__ = (
        UniqueConstraint("contest_id", "user_id", name="uq_participation"),
        UniqueConstraint("contest_id", "participant_no", name="uq_participant_number"),
        CheckConstraint(
            "status IN ('awaiting_payment','payment_pending','approved','rejected')", name="participation_status"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    contest_id: Mapped[int] = mapped_column(ForeignKey("contests.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="awaiting_payment")
    participant_no: Mapped[int | None] = mapped_column(Integer)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    user: Mapped[User] = relationship(lazy="joined")
    contest: Mapped[Contest] = relationship(lazy="joined")


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("status IN ('pending','approved','rejected')", name="payment_status"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    participation_id: Mapped[int] = mapped_column(ForeignKey("participations.id"), unique=True)
    amount_tjs: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    receipt_path: Mapped[str] = mapped_column(String(200))
    original_name: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    reject_reason: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    participation: Mapped[Participation] = relationship(lazy="joined")


class Submission(Timestamps, Base):
    __tablename__ = "submissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    participation_id: Mapped[int] = mapped_column(ForeignKey("participations.id"), unique=True)
    bot_username: Mapped[str] = mapped_column(String(32))
    bot_url: Mapped[str] = mapped_column(String(100))
    github_url: Mapped[str | None] = mapped_column(String(300))
    source_file_path: Mapped[str] = mapped_column(String(200))
    source_original_name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="submitted")
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    participation: Mapped[Participation] = relationship(lazy="joined")
    scores: Mapped[list["Score"]] = relationship(lazy="selectin")


class Score(Timestamps, Base):
    __tablename__ = "scores"
    __table_args__ = (
        UniqueConstraint("submission_id", "judge_telegram_id", name="uq_judge_score"),
        CheckConstraint(
            "functionality BETWEEN 0 AND 40 AND ui BETWEEN 0 AND 20 AND code_quality BETWEEN 0 AND 20 AND stability BETWEEN 0 AND 10 AND originality BETWEEN 0 AND 10",
            name="score_ranges",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), index=True)
    judge_telegram_id: Mapped[int] = mapped_column(BigInteger)
    functionality: Mapped[int] = mapped_column(Integer)
    ui: Mapped[int] = mapped_column(Integer)
    code_quality: Mapped[int] = mapped_column(Integer)
    stability: Mapped[int] = mapped_column(Integer)
    originality: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text, default="")

    @property
    def total(self):
        return self.functionality + self.ui + self.code_quality + self.stability + self.originality


class OAuthAttempt(Base):
    __tablename__ = "oauth_attempts"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    browser_hash: Mapped[str] = mapped_column(String(64))
    verifier: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str] = mapped_column(String(200), unique=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(100))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[int] = mapped_column(Integer)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
