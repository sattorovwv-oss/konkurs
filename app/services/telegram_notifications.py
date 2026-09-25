from sqlalchemy.orm import Session
from app.models import Notification


def enqueue(db: Session, key: str, chat_id: int, text: str, **extra):
    db.add(Notification(event_key=key, chat_id=chat_id, payload={"text": text, **extra}))
