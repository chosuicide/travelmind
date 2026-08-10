from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import (
    DEEPSEEK_MODEL,
    MAX_CHAT_MESSAGES_PER_DAY,
    MAX_CHAT_MESSAGES_PER_MINUTE,
    MAX_GLOBAL_CHAT_MESSAGES_PER_DAY,
    MAX_GLOBAL_CHAT_MESSAGES_PER_MINUTE,
    MAX_GLOBAL_GENERATIONS_PER_DAY,
    MAX_GLOBAL_GENERATIONS_PER_MINUTE,
    MAX_GENERATIONS_PER_DAY,
    MAX_GENERATIONS_PER_MINUTE,
)
from app.db import models


# === AI 使用额度：检查分钟限制和每日限制 ===
# 流程：User ID → 最近一分钟次数 → 当天次数 → 允许或返回 429
def check_generation_quota(
    user_id: int,
    db: Session,
):
    now = datetime.now(timezone.utc)
    one_minute_ago = now - timedelta(minutes=1)

    if MAX_GLOBAL_GENERATIONS_PER_MINUTE > 0:
        global_recent_count = (
            db.query(models.GenerationUsage)
            .filter(models.GenerationUsage.created_at >= one_minute_ago)
            .count()
        )
        if global_recent_count >= MAX_GLOBAL_GENERATIONS_PER_MINUTE:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="当前生成请求较多，请稍后再试。",
            )

    if MAX_GENERATIONS_PER_MINUTE > 0:
        recent_count = (
            db.query(models.GenerationUsage)
            .filter(
                models.GenerationUsage.user_id == user_id,
                models.GenerationUsage.created_at >= one_minute_ago,
            )
            .count()
        )
        if recent_count >= MAX_GENERATIONS_PER_MINUTE:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="生成请求过于频繁，请稍后再试。",
            )

    start_of_day = datetime(
        year=now.year,
        month=now.month,
        day=now.day,
        tzinfo=timezone.utc,
    )
    if MAX_GLOBAL_GENERATIONS_PER_DAY > 0:
        global_daily_count = (
            db.query(models.GenerationUsage)
            .filter(models.GenerationUsage.created_at >= start_of_day)
            .count()
        )
        if global_daily_count >= MAX_GLOBAL_GENERATIONS_PER_DAY:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="今天的公开演示生成额度已用完。",
            )

    if MAX_GENERATIONS_PER_DAY > 0:
        daily_count = (
            db.query(models.GenerationUsage)
            .filter(
                models.GenerationUsage.user_id == user_id,
                models.GenerationUsage.created_at >= start_of_day,
            )
            .count()
        )
        if daily_count >= MAX_GENERATIONS_PER_DAY:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="你今天的行程生成次数已用完。",
            )


def record_generation_usage(
    user_id: int,
    trip_id: int,
    db: Session,
):
    db.add(
        models.GenerationUsage(
            user_id=user_id,
            trip_id=trip_id,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()


# === 对话额度：限制便宜但高频的需求理解调用，并保存真实 Token ===
# 流程：分钟/日计数 → 预创建 Usage → DeepSeek → Token 回写 → 统一提交
def check_conversation_quota(user_id: int, db: Session) -> None:
    now = datetime.now(timezone.utc)
    one_minute_ago = now - timedelta(minutes=1)
    if MAX_GLOBAL_CHAT_MESSAGES_PER_MINUTE > 0:
        global_recent_count = (
            db.query(models.ConversationUsage)
            .filter(models.ConversationUsage.created_at >= one_minute_ago)
            .count()
        )
        if global_recent_count >= MAX_GLOBAL_CHAT_MESSAGES_PER_MINUTE:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="当前对话请求较多，请稍后再试。",
            )

    if MAX_CHAT_MESSAGES_PER_MINUTE > 0:
        recent_count = (
            db.query(models.ConversationUsage)
            .filter(
                models.ConversationUsage.user_id == user_id,
                models.ConversationUsage.created_at >= one_minute_ago,
            )
            .count()
        )
        if recent_count >= MAX_CHAT_MESSAGES_PER_MINUTE:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="对话请求过于频繁，请稍后再试。",
            )

    start_of_day = datetime(
        year=now.year,
        month=now.month,
        day=now.day,
        tzinfo=timezone.utc,
    )
    if MAX_GLOBAL_CHAT_MESSAGES_PER_DAY > 0:
        global_daily_count = (
            db.query(models.ConversationUsage)
            .filter(models.ConversationUsage.created_at >= start_of_day)
            .count()
        )
        if global_daily_count >= MAX_GLOBAL_CHAT_MESSAGES_PER_DAY:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="今天的公开演示对话额度已用完。",
            )

    if MAX_CHAT_MESSAGES_PER_DAY > 0:
        daily_count = (
            db.query(models.ConversationUsage)
            .filter(
                models.ConversationUsage.user_id == user_id,
                models.ConversationUsage.created_at >= start_of_day,
            )
            .count()
        )
        if daily_count >= MAX_CHAT_MESSAGES_PER_DAY:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="你今天的对话次数已用完。",
            )


def start_conversation_usage(
    user_id: int,
    conversation_id: int,
    db: Session,
) -> models.ConversationUsage:
    usage = models.ConversationUsage(
        user_id=user_id,
        conversation_id=conversation_id,
        model_name=DEEPSEEK_MODEL,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        created_at=datetime.now(timezone.utc),
    )
    db.add(usage)
    db.flush()
    return usage


def update_conversation_usage(
    usage: models.ConversationUsage,
    token_usage: dict,
) -> None:
    usage.input_tokens = int(token_usage.get("input_tokens", 0))
    usage.output_tokens = int(token_usage.get("output_tokens", 0))
    usage.total_tokens = int(
        token_usage.get("total_tokens", 0)
        or usage.input_tokens + usage.output_tokens
    )
