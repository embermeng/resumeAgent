import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.security import CurrentUser
from src.api.schemas_api import (
    ConversationList,
    ConversationSummary,
    ConversationMessages,
    MessageOut
)
from src.database.database import get_async_session
import src.database.models as models
from src.utils.time_convert import to_epoch

_log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/conversations", response_model=ConversationList)
async def conversation_list(
    db: Annotated[AsyncSession, Depends(get_async_session)],
    current_user: CurrentUser,
    page: Annotated[int, Query(description="页码", ge=1)] = 1,
    page_size: Annotated[int, Query(description="每页大小", ge=1, le=100)] = 20,
):
    """会话列表分页(降序)。"""
    total = (await db.execute(select(func.count()).select_from(models.Conversation).where(models.Conversation.user_id == current_user.id))).scalar() or 0
    result = await db.execute(
        select(models.Conversation)
        .where(models.Conversation.user_id == current_user.id)
        .order_by(models.Conversation.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size),
    )
    rows = result.scalars().all()
    conversation_list = [
        ConversationSummary(
            id=rec.id,
            title=rec.title,
            created_at=to_epoch(rec.created_at),
            updated_at=to_epoch(rec.updated_at),
        ) for rec in rows]
    return ConversationList(conversations=conversation_list, total=total)


@router.get("/conversations/{conversation_id}/messages", response_model=ConversationMessages)
async def conversation_messages(
    conversation_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_session)],
):
    """会话消息列表。"""
    conv = (await db.execute(
        select(models.Conversation).where(
            models.Conversation.id == conversation_id)
    )).scalars().first()
    if conv is None or conv.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="conversation not found")
    result = await db.execute(
        select(models.Message)
        .where(models.Message.conversation_id == conversation_id)
        .order_by(models.Message.created_at.asc(), models.Message.id.asc())
    )
    msg_list = result.scalars().all()
    if not msg_list:
        # raise HTTPException(status_code=404, detail="messages not found")
        return ConversationMessages(messages=[], conversation_id=conversation_id)

    message_list = [
        MessageOut(
            id=item.id,
            role=item.role,
            content=item.content,
            intent=item.intent,
            created_at=to_epoch(item.created_at),
        ) for item in msg_list
    ]
    return ConversationMessages(messages=message_list, conversation_id=conversation_id)
