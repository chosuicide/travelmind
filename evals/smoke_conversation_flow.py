"""真实模型 + 内存数据库端到端冒烟，不污染本地 travelmind.db。"""

import json

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.conversations.schemas import MessageCreate
from app.conversations.service import (
    create_conversation,
    get_pending_draft_preview,
    process_message,
)
from app.db import models
from app.db.session import Base


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(engine, "connect")
def enable_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)

with SessionLocal() as db:
    user = models.User(
        username="agent-smoke",
        email="agent-smoke@example.com",
        password_hash="not-used",
    )
    db.add(user)
    db.commit()
    conversation = create_conversation(db, user.id)

    for index, content in enumerate(
        ("我想去浙江省", "我想去湖州市", "明天 后天", "随便，你安排"),
        start=1,
    ):
        process_message(
            db,
            conversation,
            MessageCreate(
                client_message_id=f"real-smoke-{index}",
                content=content,
            ),
        )
        db.refresh(conversation)
        last_message = (
            db.query(models.ChatMessage)
            .filter(models.ChatMessage.conversation_id == conversation.id)
            .order_by(models.ChatMessage.id.desc())
            .first()
        )
        print(f"TURN {index}: {last_message.content}")

    preview = get_pending_draft_preview(db, conversation.id)
    assert preview is not None, "Agent did not produce a pending preview"
    assert preview.payload["candidate_draft"]["city_name"] == "湖州市"
    assert preview.payload["candidate_draft"]["people"] == 1
    assert preview.payload["candidate_draft"]["budget_flexible"] is True
    print(
        json.dumps(
            {
                "status": preview.payload["status"],
                "candidate": preview.payload["candidate_draft"],
                "assumed_fields": preview.payload["assumed_fields"],
                "start_after_apply": preview.payload["start_after_apply"],
            },
            ensure_ascii=False,
        )
    )
