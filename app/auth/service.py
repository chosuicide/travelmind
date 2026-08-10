from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from app.core.config import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    JWT_ALGORITHM,
    JWT_SECRET_KEY,
    MAX_REGISTERED_USERS,
)
from app.db import models
from app.auth.schemas import UserCreate


password_hash = PasswordHash.recommended()


class AccountLimitReached(Exception):
    """公开演示环境已经达到允许注册的账号数量。"""


class AccountAlreadyExists(Exception):
    """用户名或邮箱已经存在。"""


# === JWT 签发：把用户 ID 写入有过期时间的访问令牌 ===
# 流程：User ID → sub/exp Payload → HS256 签名 → Bearer Token
def create_access_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": str(user_id),
        "exp": expire,
    }

    return jwt.encode(
        payload,
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )


def register_user(
    db: Session,
    user_input: UserCreate,
) -> models.User:
    existing_user = (
        db.query(models.User)
        .filter(
            (models.User.email == user_input.email)
            | (models.User.username == user_input.username)
        )
        .first()
    )

    if existing_user is not None:
        raise AccountAlreadyExists

    if (
        MAX_REGISTERED_USERS > 0
        and db.query(models.User).count() >= MAX_REGISTERED_USERS
    ):
        raise AccountLimitReached

    new_user = models.User(
        username=user_input.username,
        email=user_input.email,
        password_hash=password_hash.hash(user_input.password),
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return new_user


def authenticate_user(
    db: Session,
    email: str,
    password: str,
) -> models.User | None:
    user = (
        db.query(models.User)
        .filter(models.User.email == email)
        .first()
    )

    if user is None:
        return None

    if not password_hash.verify(password, user.password_hash):
        return None

    return user
