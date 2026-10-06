from datetime import UTC, datetime
from sqlalchemy import select, update
import logging

from src.database.database import SessionLocal
import src.database.models as models

_log = logging.getLogger(__name__)


def create_conversation(title: str, user_id: int) -> int | None:
    try:
        new_record = models.Conversation(title=title[:50], user_id=user_id)
        with SessionLocal() as db:
            db.add(new_record)
            db.commit()
            return new_record.id
    except Exception:
        _log.exception("conversation插入失败: title=%s", title)
        return None


def add_message(
    conversation_id: int,
    role: str,
    content: str,
    intent: str | None = None
) -> None:
    try:
        new_record = models.Message(
            conversation_id=conversation_id, role=role, content=content, intent=intent)
        with SessionLocal() as db:
            db.execute(update(models.Conversation).where(
                models.Conversation.id == conversation_id).values(updated_at=datetime.now(UTC)))
            db.add(new_record)
            db.commit()
    except Exception:
        _log.exception("message插入失败: conversation_id=%s, role=%s, content=%s, intent=%s",
                       conversation_id, role, content, intent)


def check_conversation(conversation_id: int, user_id: int) -> bool:
    try:
        with SessionLocal() as db:
            result = db.execute(select(models.Conversation).where(
                models.Conversation.id == conversation_id, models.Conversation.user_id == user_id))
            return result.scalars().first() is not None
    except Exception:
        _log.exception(
            "检查conversation失败: conversation_id=%s, user_id=%s", conversation_id, user_id)
        return False
