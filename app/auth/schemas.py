from pydantic import BaseModel, Field


# === 认证请求：在进入业务逻辑前校验账号输入 ===
class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=8, max_length=128)


class UserLogin(BaseModel):
    email: str
    password: str
