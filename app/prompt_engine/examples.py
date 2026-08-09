# === Few-shot 示例层：集中管理行程生成的示例消息 ===
# 流程：评测证明示例有效 → 在这里加入消息 → PromptBuilder 自动装配
# 第一轮重构保持原有两条消息不变，因此暂时不加入示例。
ITINERARY_EXAMPLE_MESSAGES: tuple[dict[str, str], ...] = ()
