from app.prompt_engine.examples import ITINERARY_EXAMPLE_MESSAGES
from app.prompt_engine.formatter import (
    format_place_candidates,
    format_trip_context,
)
from app.prompt_engine.itinerary_prompt import (
    build_itinerary_instructions,
)
from app.prompt_engine.system_prompt import ITINERARY_SYSTEM_PROMPT


# === Prompt 总装配器：把固定规则、动态规则、示例和 Trip 数据组成 messages ===
# 流程：Trip → 计算天数 → system/业务/示例/user 分层装配 → DeepSeek messages
class PromptBuilder:
    def __init__(
        self,
        trip,
        place_candidates: list[dict] | None = None,
    ):
        self.trip = trip
        self.place_candidates = place_candidates

    def build(self) -> list[dict[str, str]]:
        total_days = (
            self.trip.end_date - self.trip.start_date
        ).days + 1
        candidate_pool_enabled = self.place_candidates is not None
        if candidate_pool_enabled and not self.place_candidates:
            raise ValueError("Candidate pool must not be empty")

        system_content = (
            ITINERARY_SYSTEM_PROMPT
            + build_itinerary_instructions(
                total_days,
                candidate_pool_enabled=candidate_pool_enabled,
            )
        )
        user_content = format_trip_context(self.trip)
        if self.place_candidates is not None:
            user_content += format_place_candidates(
                self.place_candidates
            )

        return [
            {
                "role": "system",
                "content": system_content,
            },
            *ITINERARY_EXAMPLE_MESSAGES,
            {
                "role": "user",
                "content": user_content,
            },
        ]
