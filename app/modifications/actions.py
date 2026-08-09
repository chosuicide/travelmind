import re
from typing import Literal


APPLY_PHRASES = {
    "确认",
    "确认修改",
    "应用修改",
    "就按这个改",
    "就这样改",
}
DISMISS_PHRASES = {
    "取消",
    "取消修改",
    "放弃修改",
    "算了不改了",
    "保持原样",
}


# === 提案动作识别：只匹配有限白名单，不让模型决定是否写数据库 ===
# 流程：去空格/标点 → 精确匹配 → apply / dismiss / None
def detect_proposal_action(
    message: str,
) -> Literal["apply", "dismiss"] | None:
    normalized = re.sub(r"[\s，。！？、,.!?]", "", message)
    if normalized in APPLY_PHRASES:
        return "apply"
    if normalized in DISMISS_PHRASES:
        return "dismiss"
    return None
