import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.schemas import UserCreate
from app.auth.demo import ensure_demo_user
from app.auth.service import (
    AccountAlreadyExists,
    AccountLimitReached,
    authenticate_user,
    register_user,
)
from app.db import models
from app.db.session import Base
from app.usage.service import (
    check_conversation_quota,
    check_generation_quota,
)


class PublicDemoLimitTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = sessionmaker(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)

    def tearDown(self):
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    @patch("app.auth.service.MAX_REGISTERED_USERS", 1)
    def test_registration_limit_rejects_a_new_account(self):
        with self.Session() as db:
            db.add(
                models.User(
                    username="existing-user",
                    email="existing@example.com",
                    password_hash="hash",
                )
            )
            db.commit()

            with self.assertRaises(AccountLimitReached):
                register_user(
                    db,
                    UserCreate(
                        username="new-user",
                        email="new@example.com",
                        password="password123",
                    ),
                )

    @patch("app.auth.service.MAX_REGISTERED_USERS", 1)
    def test_duplicate_account_error_wins_at_registration_limit(self):
        with self.Session() as db:
            db.add(
                models.User(
                    username="existing-user",
                    email="existing@example.com",
                    password_hash="hash",
                )
            )
            db.commit()

            with self.assertRaises(AccountAlreadyExists):
                register_user(
                    db,
                    UserCreate(
                        username="existing-user",
                        email="existing@example.com",
                        password="password123",
                    ),
                )

    @patch("app.usage.service.MAX_GENERATIONS_PER_MINUTE", 0)
    @patch("app.usage.service.MAX_GENERATIONS_PER_DAY", 0)
    @patch("app.usage.service.MAX_GLOBAL_GENERATIONS_PER_MINUTE", 1)
    @patch("app.usage.service.MAX_GLOBAL_GENERATIONS_PER_DAY", 0)
    def test_global_generation_limit_counts_other_users(self):
        with self.Session() as db:
            db.add(
                models.GenerationUsage(
                    user_id=2,
                    trip_id=20,
                    created_at=datetime.now(timezone.utc),
                )
            )
            db.commit()

            with self.assertRaises(HTTPException) as raised:
                check_generation_quota(user_id=1, db=db)

        self.assertEqual(raised.exception.status_code, 429)
        self.assertIn("生成请求较多", raised.exception.detail)

    @patch("app.usage.service.MAX_CHAT_MESSAGES_PER_MINUTE", 0)
    @patch("app.usage.service.MAX_CHAT_MESSAGES_PER_DAY", 0)
    @patch("app.usage.service.MAX_GLOBAL_CHAT_MESSAGES_PER_MINUTE", 1)
    @patch("app.usage.service.MAX_GLOBAL_CHAT_MESSAGES_PER_DAY", 0)
    def test_global_chat_limit_counts_other_users(self):
        with self.Session() as db:
            db.add(
                models.ConversationUsage(
                    user_id=2,
                    conversation_id=20,
                    model_name="test-model",
                    input_tokens=1,
                    output_tokens=1,
                    total_tokens=2,
                    created_at=datetime.now(timezone.utc),
                )
            )
            db.commit()

            with self.assertRaises(HTTPException) as raised:
                check_conversation_quota(user_id=1, db=db)

        self.assertEqual(raised.exception.status_code, 429)
        self.assertIn("对话请求较多", raised.exception.detail)

    def test_demo_account_seed_is_idempotent_and_login_ready(self):
        with (
            patch("app.auth.demo.SessionLocal", self.Session),
            patch("app.auth.demo.DEMO_USER_USERNAME", "resume-demo"),
            patch("app.auth.demo.DEMO_USER_EMAIL", "demo@example.com"),
            patch("app.auth.demo.DEMO_USER_PASSWORD", "password123"),
        ):
            self.assertTrue(ensure_demo_user())
            self.assertFalse(ensure_demo_user())

        with self.Session() as db:
            self.assertEqual(db.query(models.User).count(), 1)
            self.assertIsNotNone(
                authenticate_user(db, "demo@example.com", "password123")
            )


if __name__ == "__main__":
    unittest.main()
