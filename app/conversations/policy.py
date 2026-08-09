from app.conversations.state import get_missing_fields


# === 模块：数据库事实驱动的追问策略 ===
# 流程：读取已保存草稿 → 计算真实缺项 → 生成唯一下一问 → 返回可信文本
def next_question(draft: dict) -> str:
    missing = set(get_missing_fields(draft))
    province_name = draft.get("province_name")

    if "province_name" in missing:
        return "这次想去哪个省或城市？也可以直接说完整旅行想法。"
    if "city_name" in missing:
        return f"想去{province_name}的哪个城市？"
    if {"start_date", "end_date"} <= missing:
        return "计划哪天出发、哪天结束？"
    if "start_date" in missing:
        return "计划哪天出发？"
    if "end_date" in missing:
        return "计划哪天结束，或者一共玩几天？"

    conditions = []
    if "people" in missing:
        conditions.append("人数")
    if "budget" in missing:
        conditions.append("总预算")
    if conditions:
        return f"还需要知道{'和'.join(conditions)}；偏好、节奏或特殊要求可以一起说。"

    return "关键信息已经齐了。你可以继续补充偏好，也可以确认后让我开始规划。"


def assistant_reply(
    draft: dict,
    *,
    changed: bool,
    acknowledgement: str | None = None,
) -> str:
    """根据已保存草稿回复；acknowledgement 仅为兼容旧调用，不再展示。"""
    question = next_question(draft)
    if changed:
        return f"收到，我已经记下了。\n{question}"
    return question
