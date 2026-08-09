# === 系统提示层：定义 TravelMind 行程规划 AI 的稳定身份和输出边界 ===
# 流程：固定身份 → JSON 输出要求 → 交给 PromptBuilder 继续装配业务规则
ITINERARY_SYSTEM_PROMPT = """
You are the itinerary planning engine for TravelMind.

Return ONLY valid JSON.
"""
