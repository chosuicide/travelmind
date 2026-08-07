from datetime import date, datetime, timedelta, timezone
import jwt
from fastapi import Depends, FastAPI, HTTPException, status
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session
from pwdlib import PasswordHash
import models
from database import Base, engine, get_db
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from ai_service import generate_itinerary
from place_service import validate_itinerary_places
MAX_GENERATIONS_PER_MINUTE = 1
MAX_GENERATIONS_PER_DAY = 20

app = FastAPI()
password_hash = PasswordHash.recommended()
SECRET_KEY = "travelmind-dev-secret-key"  #暂时开发用，后面移到 .env
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30



# Pydantic models for request validation
class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=8, max_length=128)

# === 修改行程请求 ===
# 流程：前端发送自然语言要求 → Pydantic验证 → 交给修改接口
class TripModifyRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)

# Pydantic model for user login
class UserLogin(BaseModel):
    email: str
    password: str

# Pydantic model for trip creation
# === 创建旅行请求：验证用户提交的 Trip 是否业务合理 ===
# 流程：
# 前端 JSON
# → 检查字段类型
# → 检查长度/范围
# → 检查日期之间的关系
# → 合法才进入 POST /trips
Interest = Annotated[
    str,
    Field(min_length=1, max_length=50),
]


class TripCreate(BaseModel):
    # 去掉字符串前后空格，并拒绝我们没定义的多余字段
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    destination: str = Field(
        min_length=2,
        max_length=100,
    )

    start_date: date
    end_date: date

    budget: float = Field(
        gt=0,
        le=1_000_000,
    )

    people: int = Field(
        ge=1,
        le=20,
    )

    interests: list[Interest] = Field(
        min_length=1,
        max_length=10,
    )

    pace: Literal[
        "relaxed",
        "balanced",
        "intensive",
    ]

    notes: str | None = Field(
        default=None,
        max_length=1000,
    )

    @model_validator(mode="after")
    def validate_trip_rules(self):
        # 结束日期不能早于开始日期
        if self.end_date < self.start_date:
            raise ValueError(
                "end_date must not be earlier than start_date"
            )

        total_days = (
            self.end_date - self.start_date
        ).days + 1

        # 第一版最多规划 30 天，防止超大 AI 请求
        if total_days > 30:
            raise ValueError(
                "trip cannot be longer than 30 days"
            )

        return self

    
# === JWT Token Generation ===
def create_access_token(user_id: int):
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=ACCESS_TOKEN_EXPIRE_MINUTES
    )

    payload = {
        "sub": str(user_id),  # sub = 这个 Token 属于谁
        "exp": expire,        # Token 什么时候过期
    }

    return jwt.encode(
        payload,
        SECRET_KEY,
        algorithm=ALGORITHM,
    )

security = HTTPBearer()

# === 当前用户：验证 JWT，并从数据库找到真正的用户 ===

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    token = credentials.credentials  # 拿到 Bearer 后面的 JWT

    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
        )

        user_id = payload.get("sub")

        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )

    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    user = (
        db.query(models.User)
        .filter(models.User.id == int(user_id))
        .first()
    )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    return user


# === AI 使用额度检查 ===
# 流程：
# 当前用户
# → 查询最近 1 分钟生成次数
# → 查询当天生成次数
# → 超限返回 429
# → 未超限则允许继续

def check_generation_quota(
    user_id: int,
    db: Session,
):
    now = datetime.now(timezone.utc)

    one_minute_ago = now - timedelta(minutes=1)

    # 最近一分钟调用次数
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
            detail="Too many generation requests. Please try again later.",
        )

    # 今天 UTC 00:00
    start_of_day = datetime(
        year=now.year,
        month=now.month,
        day=now.day,
        tzinfo=timezone.utc,
    )

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
            detail="Daily generation limit reached.",
        )

# === 记录 AI 调用 ===
# 流程：准备调用 DeepSeek → 先记录本次尝试 → COMMIT → 再真正调用模型

def record_generation_usage(
    user_id: int,
    trip_id: int,
    db: Session,
):
    usage = models.GenerationUsage(
        user_id=user_id,
        trip_id=trip_id,
        created_at=datetime.now(timezone.utc),
    )

    db.add(usage)
    db.commit()



# FastAPI routes
@app.get("/")
def root():
    return {"message": "TravelMind API is running"}



@app.post("/trips", status_code=status.HTTP_201_CREATED)
def create_trip(
    trip: TripCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    new_trip = models.Trip(
        user_id=current_user.id, 
        destination=trip.destination,
        start_date=trip.start_date,
        end_date=trip.end_date,
        budget=trip.budget,
        people=trip.people,
        interests=trip.interests,
        pace=trip.pace,
        notes=trip.notes,
        status="created",
    )
#第一次添加数据库得数据到内存 第二次提交 第三次刷新存入新数据
    db.add(new_trip)
    db.commit()
    db.refresh(new_trip)

    return {
        "id": new_trip.id,
        "user_id": new_trip.user_id,
        "destination": new_trip.destination,
        "start_date": new_trip.start_date,
        "end_date": new_trip.end_date,
        "budget": new_trip.budget,
        "people": new_trip.people,
        "interests": new_trip.interests,
        "pace": new_trip.pace,
        "notes": new_trip.notes,
        "status": new_trip.status,
    }

# Create a new user注册
@app.post("/auth/register", status_code=status.HTTP_201_CREATED)
def register_user(
    user: UserCreate,
    db: Session = Depends(get_db),
):
    existing_user = (
        db.query(models.User)
        .filter(
            (models.User.email == user.email)
            | (models.User.username == user.username)
        )
        .first()
    )

    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already registered",
        )

    hashed_password = password_hash.hash(user.password)#明文传入哈希

    new_user = models.User(
        username=user.username,
        email=user.email,
        password_hash=hashed_password,
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {
        "id": new_user.id,
        "username": new_user.username,
        "email": new_user.email,
    }

# User login
@app.post("/auth/login")
def login_user(
    login: UserLogin,
    db: Session = Depends(get_db),
):
    # 先通过邮箱寻找用户
    user = (
        db.query(models.User)
        .filter(models.User.email == login.email)
        .first()
    )

    # 用户不存在，或者密码验证失败
    if user is None or not password_hash.verify(
        login.password,
        user.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # 身份验证成功，签发 Token
    access_token = create_access_token(user.id)

    return {
        "access_token": access_token,
        "token_type": "bearer",
    }



# === 查看完整旅行：查询 Trip + Days + Activities，并组装给前端 ===

@app.get("/trips/{trip_id}")
def get_trip(
    trip_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    # 先确认 Trip 存在，而且属于当前用户
    trip = (
        db.query(models.Trip)
        .filter(
            models.Trip.id == trip_id,
            models.Trip.user_id == current_user.id,
        )
        .first()
    )

    if trip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trip not found",
        )

    # 查询这个 Trip 下面的所有天
    trip_days = (
        db.query(models.TripDay)
        .filter(models.TripDay.trip_id == trip.id)
        .order_by(models.TripDay.day_number)
        .all()
    )

    days_response = []

    for day in trip_days:
        # 每一天再查询自己的 Activities
        activities = (
            db.query(models.Activity)
            .filter(models.Activity.trip_day_id == day.id)
            .order_by(models.Activity.order)
            .all()
        )

        days_response.append(
            {
                "id": day.id,
                "day_number": day.day_number,
                "date": day.date,
                "summary": day.summary,
                "activities": [
                    {
                        "id": activity.id,
                        "name": activity.name,
                        "location": activity.location,
                        "start_time": activity.start_time,
                        "end_time": activity.end_time,
                        "estimated_cost": activity.estimated_cost,
                        "description": activity.description,
                        "order": activity.order,
                        "verified_place": (
                            {
                                "provider": activity.place_provider,
                                "provider_id": activity.place_provider_id,
                                "name": activity.verified_name,
                                "address": activity.verified_address,
                                "latitude": activity.latitude,
                                "longitude": activity.longitude,
                            }
                            if activity.place_provider is not None
                            else None
                        ),
                    }
                    for activity in activities
                ],
            }
        )

    return {
        "id": trip.id,
        "destination": trip.destination,
        "start_date": trip.start_date,
        "end_date": trip.end_date,
        "budget": trip.budget,
        "people": trip.people,
        "interests": trip.interests,
        "pace": trip.pace,
        "notes": trip.notes,
        "status": trip.status,
        "days": days_response,
    }

# === 旅行列表：只返回当前登录用户自己的所有 Trip ===

@app.get("/trips")
def list_trips(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    trips = (
        db.query(models.Trip)
        .filter(models.Trip.user_id == current_user.id)  # 只查自己的
        .all()
    )

    return [
        {
            "id": trip.id,
            "destination": trip.destination,
            "start_date": trip.start_date,
            "end_date": trip.end_date,
            "budget": trip.budget,
            "people": trip.people,
            "status": trip.status,
        }
        for trip in trips
    ]

# === 删除旅行：只能删除当前用户自己的 Trip ===

@app.delete("/trips/{trip_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_trip(
    trip_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    # 只查询“这个 id + 属于当前用户”的 Trip
    trip = (
        db.query(models.Trip)
        .filter(
            models.Trip.id == trip_id,
            models.Trip.user_id == current_user.id,
        )
        .first()
    )

    if trip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trip not found",
        )

    db.delete(trip)  # 标记删除
    db.commit()      # 真正提交到数据库

# === AI 行程生成：验证权限 → 生成 → 事务保存 → 更新状态 ===

# === AI 行程生成 ===
# 流程：
# 请求
# → 验证身份和权限
# → generating
# → DeepSeek
# → 验证 AI 输出
# → 事务写 Days / Activities
# → 成功 generated
# → 失败 generation_failed

@app.post("/trips/{trip_id}/generate")
def generate_trip(
    trip_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    trip = (
        db.query(models.Trip)
        .filter(
            models.Trip.id == trip_id,
            models.Trip.user_id == current_user.id,
        )
        .first()
    )

    if trip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trip not found",
        )

    if trip.status == "generating":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Trip is already generating",
        )

    if trip.status == "generated":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Trip already generated",
        )

# === AI 调用前防线 ===
# 先检查用户额度，没资格就不要碰 DeepSeek

    check_generation_quota(
        user_id=current_user.id,
        db=db,
    )

    trip.status = "generating"
    db.commit()

    record_generation_usage(
        user_id=current_user.id,
        trip_id=trip.id,
        db=db,
    )

    try:
     # ① DeepSeek 生成结构化计划
        itinerary = generate_itinerary(trip)

        itinerary = validate_itinerary_places(
            itinerary=itinerary,
            destination=trip.destination,
        )
        # ② AI验证通过后，才开始写数据库
        for day_data in itinerary["days"]:
            trip_day = models.TripDay(
                trip_id=trip.id,
                day_number=day_data["day_number"],
                date=trip.start_date
                + timedelta(days=day_data["day_number"] - 1),
                summary=day_data["summary"],
            )

            db.add(trip_day)
            db.flush()

            for index, activity_data in enumerate(
                day_data["activities"],
                start=1,
            ):
                # === 保存 Activity：同时保存高德验证后的真实地点 ===
                # 流程：AI 活动 → verified_place → 拆出 POI 数据 → Activity → 事务提交
                verified_place = activity_data.get("verified_place")

                if verified_place is None:
                    raise ValueError(
                        "Activity is missing verified place data"
                    )

                activity = models.Activity(
                    trip_day_id=trip_day.id,
                    name=activity_data["name"],
                    location=activity_data["location"],
                    start_time=activity_data["start_time"],
                    end_time=activity_data["end_time"],
                    estimated_cost=activity_data["estimated_cost"],
                    description=activity_data["description"],
                    order=index,
                    place_provider="amap",
                    place_provider_id=verified_place.get("amap_id"),
                    verified_name=verified_place.get("name"),
                    verified_address=verified_place.get("address"),
                    latitude=verified_place.get("latitude"),
                    longitude=verified_place.get("longitude"),
                )

                db.add(activity)

        trip.status = "generated"

        # ③ 全部成功才正式提交
        db.commit()

    except Exception as exc:
        # ④ Day / Activity 写到一半也全部撤销
        db.rollback()

        # 重新取 Trip，记录失败状态
        trip = (
            db.query(models.Trip)
            .filter(models.Trip.id == trip_id)
            .first()
        )

        if trip:
            trip.status = "generation_failed"
            db.commit()

        print(f"Generation failed: {exc}")  # 开发阶段日志

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to generate itinerary",
        )

    return {
        "trip_id": trip.id,
        "status": trip.status,
        "days": itinerary["days"],
    }

# === 修改 AI 行程 ===
# 流程：
# 用户请求
# → 验证 Token 和 Trip ownership
# → 读取 Day 2 + Activities
# → 假 AI 返回修改建议
# → 后端验证 activity_id
# → 删除合法 Activity
# → COMMIT
# → 返回最新行程

@app.post("/trips/{trip_id}/modify")
def modify_trip(
    trip_id: int,
    request: TripModifyRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    # 1. 只允许修改自己的 Trip
    trip = (
        db.query(models.Trip)
        .filter(
            models.Trip.id == trip_id,
            models.Trip.user_id == current_user.id,
        )
        .first()
    )

    if trip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trip not found",
        )

    if trip.status != "generated":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Trip has not been generated yet",
        )

    # 2. 暂时固定读取 Day 2
    trip_day = (
        db.query(models.TripDay)
        .filter(
            models.TripDay.trip_id == trip.id,
            models.TripDay.day_number == 2,
        )
        .first()
    )

    if trip_day is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trip day not found",
        )

    # 3. 拿到 Day 2 当前所有活动
    activities = (
        db.query(models.Activity)
        .filter(
            models.Activity.trip_day_id == trip_day.id
        )
        .order_by(models.Activity.order)
        .all()
    )

    # 4. AI只提出“删哪些 ID”
    modification = generate_fake_modification(
        activities,
        request.message,
    )

    remove_ids = modification["remove_activity_ids"]

    # 5. 后端验证 AI 提出的 ID
    valid_activity_ids = {
        activity.id
        for activity in activities
    }

    for activity_id in remove_ids:
        if activity_id not in valid_activity_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="AI returned an invalid activity",
            )

    # 6. 真正修改数据库
    try:
        for activity in activities:
            if activity.id in remove_ids:
                db.delete(activity)

        db.commit()

    except Exception:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to modify itinerary",
        )

    return {
        "trip_id": trip.id,
        "message": request.message,
        "removed_activity_ids": remove_ids,
        "status": "modified",
    }
