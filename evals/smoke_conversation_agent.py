"""手动真实模型冒烟：验证委托表达会进入预览且回复服从后端状态。"""

import json

from app.conversations.extractor import extract_message, generate_grounded_reply


draft = {
    "province_code": "330000",
    "province_name": "浙江省",
    "city_code": "330500",
    "city_name": "湖州市",
    "start_date": "2026-08-10",
    "end_date": "2026-08-11",
    "duration_days": 2,
}
history = [
    {"role": "user", "content": "我想去浙江省"},
    {
        "role": "assistant",
        "content": "浙江有不少适合慢慢玩的地方，你更想去哪座城市？",
    },
    {"role": "user", "content": "我想去湖州市"},
    {
        "role": "assistant",
        "content": "湖州很适合轻松走走，你大概什么时候出发？",
    },
    {"role": "user", "content": "明天 后天"},
]

decision = extract_message(draft, "随便，你安排", history=history)
print(
    json.dumps(
        {
            "intent": decision.intent,
            "patch": decision.patch.model_dump(mode="json", exclude_none=True),
            "add_interests": decision.add_interests,
            "prepare_preview": decision.prepare_preview,
            "use_defaults": decision.use_defaults,
            "start_generation": decision.start_generation,
        },
        ensure_ascii=False,
    )
)

preview = {
    "candidate_draft": {
        **draft,
        "people": 1,
        "budget_flexible": True,
        "interests": ["城市漫游", "本地体验"],
        "pace": "balanced",
    },
    "assumed_fields": ["people", "budget", "interests", "pace"],
}
reply, usage = generate_grounded_reply(
    draft,
    "随便，你安排",
    history=history,
    accepted=False,
    missing_fields=[],
    preview=preview,
)
print(reply)
print(json.dumps({"total_tokens": usage["total_tokens"]}))
