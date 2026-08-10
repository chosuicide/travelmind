from app.auth.schemas import UserCreate
from app.auth.service import password_hash
from app.core.config import (
    DEMO_USER_EMAIL,
    DEMO_USER_PASSWORD,
    DEMO_USER_USERNAME,
)
from app.db import models
from app.db.session import SessionLocal


# === 演示账号：生产容器每次启动时保证简历里的测试凭据可用 ===
# 流程：校验环境变量 → 查找邮箱/用户名 → 创建或重置演示账号 → 提交
def ensure_demo_user() -> bool:
    configured_values = (
        DEMO_USER_USERNAME,
        DEMO_USER_EMAIL,
        DEMO_USER_PASSWORD,
    )
    if not any(configured_values):
        return False
    if not all(configured_values):
        raise RuntimeError(
            "DEMO_USER_USERNAME, DEMO_USER_EMAIL and DEMO_USER_PASSWORD "
            "must be configured together"
        )

    demo_input = UserCreate(
        username=DEMO_USER_USERNAME,
        email=DEMO_USER_EMAIL,
        password=DEMO_USER_PASSWORD,
    )

    with SessionLocal() as db:
        user_by_email = (
            db.query(models.User)
            .filter(models.User.email == demo_input.email)
            .first()
        )
        user_by_username = (
            db.query(models.User)
            .filter(models.User.username == demo_input.username)
            .first()
        )
        if (
            user_by_username is not None
            and user_by_email is not None
            and user_by_username.id != user_by_email.id
        ) or (user_by_username is not None and user_by_email is None):
            raise RuntimeError("The configured demo username is already in use")

        demo_user = user_by_email
        created = demo_user is None
        if demo_user is None:
            demo_user = models.User(
                username=demo_input.username,
                email=demo_input.email,
                password_hash="",
            )
            db.add(demo_user)

        demo_user.username = demo_input.username
        demo_user.password_hash = password_hash.hash(demo_input.password)
        db.commit()
        return created
