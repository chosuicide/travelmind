import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.trips.schemas import TripCreate


CASES_PATH = Path(__file__).with_name("itinerary_cases.json")


# === 评测案例协议：在真实 Trip 输入规则之上增加稳定案例编号 ===
# 流程：JSON 案例 → Pydantic → 复用 TripCreate 业务校验 → 评测输入
class ItineraryEvaluationCase(TripCreate):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    case_id: str = Field(min_length=1, max_length=100)
    tags: list[str] = Field(min_length=1, max_length=10)

    def trip_data(self) -> dict:
        return self.model_dump(exclude={"case_id", "tags"})

    @property
    def is_smoke_case(self) -> bool:
        return "smoke" in self.tags


def load_itinerary_cases(
    path: Path = CASES_PATH,
) -> list[ItineraryEvaluationCase]:
    raw_cases = json.loads(path.read_text(encoding="utf-8"))
    cases = [
        ItineraryEvaluationCase.model_validate(raw_case)
        for raw_case in raw_cases
    ]

    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Evaluation case_id values must be unique")

    return cases
