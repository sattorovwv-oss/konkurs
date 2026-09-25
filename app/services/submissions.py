from sqlalchemy import select
from app.models import Participation, Submission, Score, AuditLog
from app.services.contest_state import locked_contest, submission_open
from app.services.errors import DomainError
from app.services.ranking import get_submissions, rank_submissions
from app.services.telegram_notifications import enqueue
from app.config import get_settings
from app.utils.time import now


def submit_project(db, participation_id, user, data, stored=None):
    p = db.get(Participation, participation_id)
    if not p or p.user_id != user.id:
        raise DomainError("Участие не найдено.", 404)
    c = locked_contest(db, p.contest_id)
    db.refresh(p)
    if p.status != "approved":
        raise DomainError("Сначала дождитесь подтверждения участия.", 403)
    if not submission_open(c):
        raise DomainError("Приём работ завершён или конкурс заморожен.", 409)
    sub = db.scalar(select(Submission).where(Submission.participation_id == p.id))
    old = None
    if not sub:
        if not stored:
            raise DomainError("Прикрепите ZIP с исходным кодом.")
        sub = Submission(participation_id=p.id)
        db.add(sub)
    elif sub.scores:
        raise DomainError("Оценённую работу нельзя изменить.", 409)
    if stored:
        old = sub.source_file_path
        sub.source_file_path, sub.source_original_name = stored.key, stored.name
    for k, v in data.model_dump().items():
        setattr(sub, k, v)
    sub.bot_url = "https://t.me/" + data.bot_username
    # Tie-break uses the last submitted version, not an early empty placeholder.
    sub.submitted_at = sub.updated_at = now()
    sub.status = "submitted"
    db.flush()
    return sub, old


def save_score(db, submission_id, actor_id, data):
    if actor_id not in get_settings().admin_ids:
        raise DomainError("Доступ запрещён.", 403)
    sub = db.get(Submission, submission_id)
    if not sub:
        raise DomainError("Работа не найдена.", 404)
    c = locked_contest(db, sub.participation.contest_id)
    if c.status != "finished" or c.results_published:
        raise DomainError("Оценивание доступно после завершения приёма работ и до публикации результатов.", 409)
    score = db.scalar(select(Score).where(Score.submission_id == sub.id, Score.judge_telegram_id == actor_id))
    if not score:
        score = Score(submission_id=sub.id, judge_telegram_id=actor_id)
        db.add(score)
    for k, v in data.model_dump().items():
        setattr(score, k, v)
    sub.status = "reviewed"
    db.add(AuditLog(actor_id=actor_id, action="score:saved", entity_id=sub.id))
    db.flush()
    db.expire(sub, ["scores"])
    return score


def publish_results(db, contest_id, actor_id):
    if actor_id not in get_settings().admin_ids:
        raise DomainError("Доступ запрещён.", 403)
    c = locked_contest(db, contest_id)
    if c.status != "finished" or c.results_published:
        raise DomainError("Сначала завершите конкурс. Повторная публикация запрещена.", 409)
    submissions = get_submissions(db, c.id)
    if not submissions or any(not sub.scores for sub in submissions):
        raise DomainError("Нельзя опубликовать результаты: оцените все отправленные работы.", 409)
    rows = rank_submissions(submissions, c)
    c.results_published, c.results_published_at = True, now()
    s = get_settings()
    for row in rows:
        message = (
            f"🏆 Поздравляем! Приз: {row.prize} TJS\nЗабрать приз: {s.prize_contact_url}"
            if row.rank <= 3
            else "💚 Спасибо за участие! Продолжайте развиваться и возвращайтесь на следующие конкурсы."
        )
        enqueue(
            db,
            f"result:{c.id}:{row.participant.id}",
            row.participant.user.telegram_id,
            f"{c.title}\nМесто: #{row.rank}\nРезультат: {row.total:.2f} / 100\n{message}\n{s.app_url}/contests/{c.slug}/leaderboard",
        )
    submitted_ids = {row.participant.id for row in rows}
    for p in db.scalars(
        select(Participation).where(Participation.contest_id == c.id, Participation.status == "approved")
    ):
        if p.id not in submitted_ids:
            enqueue(
                db,
                f"result:{c.id}:{p.id}",
                p.user.telegram_id,
                f"💚 Итоги «{c.title}» опубликованы. Вы не отправили проект и не включены в рейтинг. Ждём вас в следующих конкурсах!\n{s.app_url}/contests/{c.slug}/leaderboard",
            )
    db.add(AuditLog(actor_id=actor_id, action="results:published", entity_id=c.id))
    db.flush()
    return rows
