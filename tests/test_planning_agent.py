import copy
import json
import sqlite3
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import ValidationError

from app.agent.context import PlanningContext
from app.agent.runner import PlanningAgent
from app.agent.tools import execute_travel_tool
from app.integrations.amap import search_place_candidates


def _candidate(
    poi_id: str = "poi-1",
    name: str = "陈家祠",
) -> dict:
    return {
        "amap_id": poi_id,
        "name": name,
        "address": "中山七路恩龙里34号",
        "city": "广州市",
        "district": "荔湾区",
        "latitude": 23.1293,
        "longitude": 113.2466,
        "type": "风景名胜;旅游景点",
        "typecode": "110000",
    }


def _tool_call(
    arguments: str,
    call_id: str = "call-1",
    name: str = "search_places",
):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(
            name=name,
            arguments=arguments,
        ),
    )


def _response(*, content=None, tool_calls=None):
    message = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)]
    )


class _FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        return self.responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.chat = SimpleNamespace(
            completions=_FakeCompletions(responses)
        )


def _trip():
    return SimpleNamespace(
        destination="广州",
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 1),
        budget=1000,
        people=2,
        interests=["历史文化"],
        pace="relaxed",
        notes=None,
    )


def _final_itinerary(place_id: str = "poi-1") -> str:
    return json.dumps(
        {
            "days": [
                {
                    "day_number": 1,
                    "summary": "荔湾历史文化之旅",
                    "activities": [
                        {
                            "place_provider_id": place_id,
                            "name": "模型填写的别名",
                            "location": "模型填写的地址",
                            "start_time": "10:00",
                            "end_time": "12:00",
                            "estimated_cost": 20,
                            "description": "参观岭南建筑",
                        }
                    ],
                }
            ]
        },
        ensure_ascii=False,
    )


def _search_tool_result(arguments: str, candidates: list[dict]) -> dict:
    return {
        "content": {
            "query": json.loads(arguments),
            "candidates": [
                {
                    "place_provider_id": candidate["amap_id"],
                    "name": candidate["name"],
                }
                for candidate in candidates
            ],
        },
        "places": candidates,
    }


# === Tool-Calling Agent 测试：证明模型只能使用本轮工具返回的真实 POI ===
# 流程：模拟 tool_call → 模拟高德候选 → 模拟最终 JSON → 安全边界断言
class PlanningAgentTests(unittest.TestCase):
    def test_sqlite_checkpoint_resumes_after_tool_node_failure(self):
        arguments = json.dumps(
            {
                "keywords": "历史建筑",
                "district": "荔湾区",
                "category": "attraction",
                "limit": 3,
            }
        )
        client = _FakeClient(
            [
                _response(tool_calls=[_tool_call(arguments)]),
                _response(content=_final_itinerary()),
            ]
        )

        class FlakyToolExecutor:
            def __init__(self):
                self.calls = 0

            def __call__(self, name, raw_arguments, context):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("temporary tool failure")
                return _search_tool_result(raw_arguments, [_candidate()])

        executor = FlakyToolExecutor()
        agent = PlanningAgent(
            client=client,
            model="test-model",
            tool_executor=executor,
            max_tool_calls=4,
            max_model_turns=4,
            thread_id="generation-checkpoint-test",
        )

        connection = sqlite3.connect(":memory:", check_same_thread=False)
        saver = SqliteSaver(
            connection,
            serde=JsonPlusSerializer(
                allowed_msgpack_modules=[
                    ("app.agent.context", "PlanningContext"),
                ]
            ),
        )
        try:
            with patch(
                "app.agent.checkpoint.get_planning_checkpointer",
                return_value=saver,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "temporary tool failure",
                ):
                    agent.run(_trip())
                itinerary = agent.run(_trip())
        finally:
            connection.close()

        self.assertEqual(executor.calls, 2)
        self.assertEqual(len(client.chat.completions.requests), 2)
        self.assertEqual(
            itinerary["days"][0]["activities"][0]["name"],
            "陈家祠",
        )

    def test_external_tool_failure_is_traced_before_run_fails(self):
        arguments = json.dumps(
            {
                "keywords": "历史建筑",
                "district": "荔湾区",
                "category": "attraction",
                "limit": 3,
            }
        )
        client = _FakeClient(
            [_response(tool_calls=[_tool_call(arguments)])]
        )
        tool_trace = []

        with self.assertRaisesRegex(RuntimeError, "AMap unavailable"):
            PlanningAgent(
                client=client,
                model="test-model",
                tool_executor=Mock(
                    side_effect=RuntimeError("AMap unavailable")
                ),
                on_tool_result=tool_trace.append,
            ).run(_trip())

        self.assertEqual(tool_trace[0]["status"], "failed")
        self.assertEqual(tool_trace[0]["error"], "RuntimeError")

    def test_final_draft_is_always_validated_by_graph(self):
        search_arguments = json.dumps(
            {
                "keywords": "岭南历史建筑",
                "district": "荔湾区",
                "category": "attraction",
                "limit": 3,
            }
        )
        client = _FakeClient(
            [
                _response(
                    tool_calls=[_tool_call(search_arguments)]
                ),
                _response(content=_final_itinerary()),
            ]
        )

        def executor(tool_name, raw_arguments, context):
            if tool_name == "search_places":
                return _search_tool_result(
                    raw_arguments,
                    [_candidate()],
                )
            raise AssertionError(f"unexpected tool: {tool_name}")

        quality_trace = []
        itinerary = PlanningAgent(
            client=client,
            model="test-model",
            tool_executor=executor,
            on_quality_result=quality_trace.append,
        ).run(_trip())

        self.assertEqual(len(client.chat.completions.requests), 2)
        self.assertEqual(
            itinerary["days"][0]["activities"][0]["name"],
            "陈家祠",
        )
        self.assertEqual(len(quality_trace), 1)
        self.assertEqual(quality_trace[0]["stage"], "initial")
        self.assertEqual(quality_trace[0]["issues"], [])
        self.assertEqual(quality_trace[0]["warning_penalty"], 0)
        self.assertEqual(quality_trace[0]["hard_issue_count"], 0)

    def test_agent_observes_tool_result_then_returns_bound_itinerary(self):
        arguments = json.dumps(
            {
                "keywords": "岭南历史建筑",
                "district": "荔湾区",
                "category": "attraction",
                "limit": 3,
            }
        )
        search_response = _response(
            tool_calls=[_tool_call(arguments)]
        )
        search_response.usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
        )
        final_response = _response(content=_final_itinerary())
        final_response.usage = SimpleNamespace(
            prompt_tokens=200,
            completion_tokens=30,
            total_tokens=230,
        )
        client = _FakeClient([search_response, final_response])
        executor = Mock(
            return_value=_search_tool_result(
                arguments,
                [_candidate()],
            )
        )
        tool_trace = []
        usage_trace = []

        itinerary = PlanningAgent(
            client=client,
            model="test-model",
            tool_executor=executor,
            on_tool_result=tool_trace.append,
            on_model_usage=usage_trace.append,
        ).run(_trip())

        activity = itinerary["days"][0]["activities"][0]
        self.assertEqual(activity["name"], "陈家祠")
        self.assertEqual(activity["location"], "中山七路恩龙里34号")
        self.assertEqual(
            activity["verified_place"]["amap_id"],
            "poi-1",
        )
        second_messages = (
            client.chat.completions.requests[1]["messages"]
        )
        self.assertEqual(second_messages[-1]["role"], "tool")
        self.assertIn("poi-1", second_messages[-1]["content"])
        tool_observation = json.loads(second_messages[-1]["content"])
        self.assertEqual(
            tool_observation["tool_budget"]["remaining_total"],
            7,
        )
        self.assertEqual(
            tool_observation["tool_budget"]["remaining_by_tool"]
            ["search_places"],
            3,
        )
        self.assertEqual(
            client.chat.completions.requests[0]["tool_choice"],
            {
                "type": "function",
                "function": {"name": "search_places"},
            },
        )
        self.assertFalse(
            client.chat.completions.requests[0]["parallel_tool_calls"]
        )
        self.assertIn(
            "at most 8 tool calls",
            client.chat.completions.requests[0]["messages"][0]["content"],
        )
        self.assertEqual(tool_trace[0]["tool_name"], "search_places")
        self.assertEqual(tool_trace[0]["candidate_count"], 1)
        self.assertEqual(
            tool_trace[0]["tool_budget"]["remaining_total"],
            7,
        )
        self.assertEqual(
            usage_trace,
            [
                {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                },
                {
                    "input_tokens": 200,
                    "output_tokens": 30,
                    "total_tokens": 230,
                },
            ],
        )

    def test_agent_rejects_place_not_seen_in_tool_results(self):
        arguments = json.dumps(
            {
                "keywords": "历史建筑",
                "district": "荔湾区",
                "category": "attraction",
                "limit": 3,
            }
        )
        client = _FakeClient(
            [
                _response(tool_calls=[_tool_call(arguments)]),
                _response(content=_final_itinerary("invented-poi")),
                _response(content=_final_itinerary("invented-poi")),
            ]
        )
        executor = Mock(
            return_value=_search_tool_result(
                arguments,
                [_candidate()],
            )
        )

        with self.assertRaisesRegex(
            ValueError,
            "not returned by a tool",
        ):
            PlanningAgent(
                client=client,
                model="test-model",
                tool_executor=executor,
            ).run(_trip())

    def test_agent_repairs_quality_once_without_more_tools(self):
        arguments = json.dumps(
            {
                "keywords": "历史景点",
                "district": "荔湾区",
                "category": "attraction",
                "limit": 5,
            }
        )
        first_answer = json.loads(_final_itinerary())
        template = first_answer["days"][0]["activities"][0]
        first_answer["days"][0]["activities"] = [
            {
                **template,
                "place_provider_id": f"poi-{index}",
                "name": f"候选景点{index}",
                "start_time": f"{8 + index:02d}:00",
                "end_time": f"{9 + index:02d}:00",
            }
            for index in range(1, 5)
        ]
        repaired_answer = copy.deepcopy(first_answer)
        repaired_answer["days"][0]["activities"] = (
            repaired_answer["days"][0]["activities"][:3]
        )
        quality_trace = []
        client = _FakeClient(
            [
                _response(tool_calls=[_tool_call(arguments)]),
                _response(
                    content=json.dumps(
                        first_answer,
                        ensure_ascii=False,
                    )
                ),
                _response(
                    content=json.dumps(
                        repaired_answer,
                        ensure_ascii=False,
                    )
                ),
            ]
        )
        executor = Mock(
            return_value=_search_tool_result(
                arguments,
                [
                    _candidate(
                        f"poi-{index}",
                        f"候选景点{index}",
                    )
                    for index in range(1, 5)
                ],
            )
        )
        itinerary = PlanningAgent(
            client=client,
            model="test-model",
            tool_executor=executor,
            on_quality_result=quality_trace.append,
        ).run(_trip())

        self.assertEqual(len(itinerary["days"][0]["activities"]), 3)
        self.assertEqual(executor.call_count, 1)
        self.assertEqual(
            client.chat.completions.requests[2]["tool_choice"],
            "none",
        )
        self.assertEqual(quality_trace[0]["stage"], "initial")
        self.assertTrue(quality_trace[0]["issues"])
        self.assertEqual(quality_trace[1]["stage"], "after_repair")
        self.assertEqual(quality_trace[1]["issues"], [])
        self.assertEqual(quality_trace[2]["stage"], "selection")
        self.assertEqual(quality_trace[2]["selected"], "repaired")

    def test_agent_keeps_original_when_repair_does_not_improve(self):
        arguments = json.dumps(
            {
                "keywords": "景点",
                "district": "荔湾区",
                "category": "attraction",
                "limit": 5,
            }
        )
        first_answer = json.loads(_final_itinerary())
        template = first_answer["days"][0]["activities"][0]

        def answer_with_ids(place_ids):
            answer = copy.deepcopy(first_answer)
            answer["days"][0]["activities"] = [
                {
                    **template,
                    "place_provider_id": place_id,
                    "name": place_id,
                    "start_time": f"{8 + index:02d}:00",
                    "end_time": f"{9 + index:02d}:00",
                }
                for index, place_id in enumerate(place_ids, start=1)
            ]
            return json.dumps(answer, ensure_ascii=False)

        client = _FakeClient(
            [
                _response(tool_calls=[_tool_call(arguments)]),
                _response(
                    content=answer_with_ids(
                        ["poi-1", "poi-2", "poi-3", "poi-4"]
                    )
                ),
                _response(
                    content=answer_with_ids(
                        ["poi-2", "poi-3", "poi-4", "poi-5"]
                    )
                ),
            ]
        )
        executor = Mock(
            return_value=_search_tool_result(
                arguments,
                [
                    _candidate(f"poi-{index}", f"景点{index}")
                    for index in range(1, 6)
                ],
            )
        )

        quality_trace = []
        itinerary = PlanningAgent(
            client=client,
            model="test-model",
            tool_executor=executor,
            on_quality_result=quality_trace.append,
        ).run(_trip())

        self.assertEqual(
            [
                activity["place_provider_id"]
                for activity in itinerary["days"][0]["activities"]
            ],
            ["poi-1", "poi-2", "poi-3", "poi-4"],
        )
        self.assertEqual(quality_trace[-1]["stage"], "selection")
        self.assertEqual(quality_trace[-1]["selected"], "original")

    def test_agent_keeps_improved_repair_with_same_warning_count(self):
        arguments = json.dumps(
            {
                "keywords": "景点",
                "district": "荔湾区",
                "category": "attraction",
                "limit": 3,
            }
        )
        template = json.loads(_final_itinerary())["days"][0]["activities"][0]

        def answer_with_destination(destination_id):
            return json.dumps(
                {
                    "days": [
                        {
                            "day_number": 1,
                            "summary": "城市景点游览",
                            "activities": [
                                {
                                    **template,
                                    "place_provider_id": "poi-1",
                                    "start_time": "09:00",
                                    "end_time": "11:00",
                                },
                                {
                                    **template,
                                    "place_provider_id": destination_id,
                                    "start_time": "13:00",
                                    "end_time": "15:00",
                                },
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            )

        candidates = [
            _candidate("poi-1", "起点"),
            _candidate("poi-2", "远处景点"),
            _candidate("poi-3", "较近景点"),
        ]
        candidates[0].update({"latitude": 23.0, "longitude": 113.0})
        candidates[1].update({"latitude": 23.0, "longitude": 113.3})
        candidates[2].update({"latitude": 23.0, "longitude": 113.15})
        client = _FakeClient(
            [
                _response(tool_calls=[_tool_call(arguments)]),
                _response(content=answer_with_destination("poi-2")),
                _response(content=answer_with_destination("poi-3")),
            ]
        )
        quality_trace = []

        itinerary = PlanningAgent(
            client=client,
            model="test-model",
            tool_executor=Mock(
                return_value=_search_tool_result(arguments, candidates)
            ),
            on_quality_result=quality_trace.append,
        ).run(_trip())

        self.assertEqual(
            itinerary["days"][0]["activities"][1]["place_provider_id"],
            "poi-3",
        )
        self.assertEqual(len(quality_trace[0]["issues"]), 1)
        self.assertEqual(len(quality_trace[1]["issues"]), 1)
        self.assertLess(
            quality_trace[-1]["repaired_penalty"],
            quality_trace[-1]["original_penalty"],
        )
        self.assertEqual(quality_trace[-1]["selected"], "repaired")

    def test_agent_fails_when_hard_overlap_survives_repair(self):
        arguments = json.dumps(
            {
                "keywords": "景点",
                "district": "荔湾区",
                "category": "attraction",
                "limit": 2,
            }
        )
        template = json.loads(_final_itinerary())["days"][0]["activities"][0]

        def overlapping_answer(second_start):
            return json.dumps(
                {
                    "days": [
                        {
                            "day_number": 1,
                            "summary": "城市景点游览",
                            "activities": [
                                {
                                    **template,
                                    "place_provider_id": "poi-1",
                                    "start_time": "09:00",
                                    "end_time": "12:00",
                                },
                                {
                                    **template,
                                    "place_provider_id": "poi-2",
                                    "start_time": second_start,
                                    "end_time": "14:00",
                                },
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            )

        client = _FakeClient(
            [
                _response(tool_calls=[_tool_call(arguments)]),
                _response(content=overlapping_answer("11:00")),
                _response(content=overlapping_answer("11:30")),
            ]
        )
        executor = Mock(
            return_value=_search_tool_result(
                arguments,
                [_candidate("poi-1", "景点一"), _candidate("poi-2", "景点二")],
            )
        )

        with self.assertRaisesRegex(ValueError, "hard itinerary issues"):
            PlanningAgent(
                client=client,
                model="test-model",
                tool_executor=executor,
            ).run(_trip())

    def test_agent_keeps_valid_original_when_repair_duplicates_poi(self):
        arguments = json.dumps(
            {
                "keywords": "历史景点",
                "district": "荔湾区",
                "category": "attraction",
                "limit": 4,
            }
        )
        original = json.loads(_final_itinerary())
        template = original["days"][0]["activities"][0]
        original["days"][0]["activities"] = [
            {
                **template,
                "place_provider_id": f"poi-{index}",
                "name": f"景点{index}",
                "start_time": f"{8 + index:02d}:00",
                "end_time": f"{9 + index:02d}:00",
            }
            for index in range(1, 5)
        ]
        invalid_repair = copy.deepcopy(original)
        invalid_repair["days"][0]["activities"] = (
            invalid_repair["days"][0]["activities"][:3]
        )
        invalid_repair["days"][0]["activities"][1][
            "place_provider_id"
        ] = "poi-1"
        quality_trace = []
        client = _FakeClient(
            [
                _response(tool_calls=[_tool_call(arguments)]),
                _response(
                    content=json.dumps(original, ensure_ascii=False)
                ),
                _response(
                    content=json.dumps(
                        invalid_repair,
                        ensure_ascii=False,
                    )
                ),
            ]
        )
        executor = Mock(
            return_value=_search_tool_result(
                arguments,
                [
                    _candidate(f"poi-{index}", f"景点{index}")
                    for index in range(1, 5)
                ],
            )
        )

        itinerary = PlanningAgent(
            client=client,
            model="test-model",
            tool_executor=executor,
            on_quality_result=quality_trace.append,
        ).run(_trip())

        self.assertEqual(
            len(itinerary["days"][0]["activities"]),
            4,
        )
        self.assertEqual(
            [event["stage"] for event in quality_trace],
            ["initial", "repair_rejected"],
        )
        self.assertIn(
            "duplicate POI",
            quality_trace[1]["issues"][0]["message"],
        )

    def test_mandatory_validation_repairs_invalid_draft(self):
        search_arguments = json.dumps(
            {
                "keywords": "历史景点",
                "district": "荔湾区",
                "category": "attraction",
                "limit": 3,
            }
        )
        invalid_draft = json.loads(_final_itinerary())
        template = invalid_draft["days"][0]["activities"][0]
        invalid_draft["days"][0]["activities"] = [
            {
                **template,
                "place_provider_id": f"poi-{index}",
                "name": f"景点{index}",
                "start_time": f"{8 + index:02d}:00",
                "end_time": f"{9 + index:02d}:00",
            }
            for index in range(1, 5)
        ]
        valid_draft = copy.deepcopy(invalid_draft)
        valid_draft["days"][0]["activities"] = (
            valid_draft["days"][0]["activities"][:3]
        )
        client = _FakeClient(
            [
                _response(
                    tool_calls=[_tool_call(search_arguments)]
                ),
                _response(
                    content=json.dumps(invalid_draft, ensure_ascii=False)
                ),
                _response(
                    content=json.dumps(valid_draft, ensure_ascii=False)
                ),
            ]
        )

        def executor(tool_name, raw_arguments, context):
            if tool_name == "search_places":
                return _search_tool_result(
                    raw_arguments,
                    [
                        _candidate(f"poi-{index}", f"景点{index}")
                        for index in range(1, 5)
                    ],
                )
            raise AssertionError(f"unexpected tool: {tool_name}")

        itinerary = PlanningAgent(
            client=client,
            model="test-model",
            tool_executor=executor,
        ).run(_trip())

        self.assertEqual(len(itinerary["days"][0]["activities"]), 3)
        third_messages = client.chat.completions.requests[2]["messages"]
        self.assertEqual(third_messages[-1]["role"], "user")
        self.assertIn("Quality issues", third_messages[-1]["content"])

    def test_agent_repairs_incorrect_day_count_once(self):
        trip = _trip()
        trip.end_date = date(2026, 10, 2)
        arguments = json.dumps(
            {
                "keywords": "历史景点",
                "district": "荔湾区",
                "category": "attraction",
                "limit": 2,
            }
        )
        one_day = json.loads(_final_itinerary("poi-1"))
        two_days = copy.deepcopy(one_day)
        second_day = copy.deepcopy(two_days["days"][0])
        second_day["day_number"] = 2
        second_day["summary"] = "第二天"
        second_day["activities"][0]["place_provider_id"] = "poi-2"
        two_days["days"].append(second_day)
        client = _FakeClient(
            [
                _response(tool_calls=[_tool_call(arguments)]),
                _response(
                    content=json.dumps(one_day, ensure_ascii=False)
                ),
                _response(
                    content=json.dumps(two_days, ensure_ascii=False)
                ),
            ]
        )
        executor = Mock(
            return_value=_search_tool_result(
                arguments,
                [
                    _candidate("poi-1", "陈家祠堂"),
                    _candidate("poi-2", "沙面岛"),
                ],
            )
        )

        itinerary = PlanningAgent(
            client=client,
            model="test-model",
            tool_executor=executor,
        ).run(trip)

        self.assertEqual(len(itinerary["days"]), 2)
        self.assertEqual(executor.call_count, 1)
        self.assertEqual(
            client.chat.completions.requests[2]["tool_choice"],
            "none",
        )
        repair_prompt = (
            client.chat.completions.requests[2]["messages"][-1][
                "content"
            ]
        )
        self.assertIn("exactly 2 days", repair_prompt)

    def test_agent_forces_final_answer_after_tool_budget_is_used(self):
        arguments = json.dumps(
            {
                "keywords": "景点",
                "district": "荔湾区",
                "category": "attraction",
                "limit": 1,
            }
        )
        client = _FakeClient(
            [
                _response(
                    tool_calls=[
                        _tool_call(arguments, "call-1"),
                        _tool_call(arguments, "call-2"),
                    ]
                ),
                _response(content=_final_itinerary()),
            ]
        )
        executor = Mock(
            return_value=_search_tool_result(
                arguments,
                [_candidate()],
            )
        )
        tool_trace = []

        itinerary = PlanningAgent(
            client=client,
            model="test-model",
            tool_executor=executor,
            max_tool_calls=1,
            on_tool_result=tool_trace.append,
        ).run(_trip())

        self.assertEqual(
            itinerary["days"][0]["activities"][0][
                "place_provider_id"
            ],
            "poi-1",
        )
        self.assertEqual(executor.call_count, 1)
        self.assertEqual(
            [event["status"] for event in tool_trace],
            ["succeeded", "rejected"],
        )
        self.assertEqual(
            tool_trace[1]["tool_budget"]["remaining_total"],
            0,
        )
        second_request = client.chat.completions.requests[1]
        self.assertEqual(second_request["tool_choice"], "none")
        self.assertIn(
            "tool budget is exhausted",
            second_request["messages"][-1]["content"].lower(),
        )

    @patch("app.agent.tools.search_place_candidates")
    def test_tool_validates_arguments_and_locks_trip_destination(
        self,
        mock_search,
    ):
        mock_search.return_value = [_candidate()]
        context = PlanningContext(destination="广州")

        result = execute_travel_tool(
            "search_places",
            json.dumps(
                {
                    "keywords": "早茶",
                    "district": "荔湾区",
                    "category": "restaurant",
                    "limit": 5,
                }
            ),
            context,
        )

        self.assertEqual(result["places"][0]["amap_id"], "poi-1")
        mock_search.assert_called_once_with(
            destination="广州",
            keywords="早茶",
            district="荔湾区",
            category="restaurant",
            limit=5,
        )

        with self.assertRaises(ValidationError):
            execute_travel_tool(
                "search_places",
                json.dumps(
                    {
                        "keywords": "早茶",
                        "district": "荔湾区",
                        "category": "restaurant",
                        "limit": 50,
                    }
                ),
                context,
            )
        self.assertEqual(mock_search.call_count, 1)

    @patch("app.integrations.amap.httpx.get")
    def test_amap_agent_search_applies_category_and_result_limit(
        self,
        mock_get,
    ):
        response = Mock()
        response.json.return_value = {
            "status": "1",
            "pois": [
                {
                    "id": "poi-1",
                    "name": "陈家祠",
                    "address": "中山七路恩龙里34号",
                    "cityname": "广州市",
                    "adname": "荔湾区",
                    "location": "113.2466,23.1293",
                    "type": "风景名胜;旅游景点",
                    "typecode": "110000",
                }
            ],
        }
        mock_get.return_value = response

        candidates = search_place_candidates(
            destination="广州",
            keywords="历史建筑",
            district="荔湾区",
            category="attraction",
            limit=3,
        )

        self.assertEqual(candidates[0]["amap_id"], "poi-1")
        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["region"], "广州")
        self.assertEqual(params["keywords"], "荔湾区 历史建筑")
        self.assertEqual(params["types"], "110000")
        self.assertEqual(params["page_size"], 3)

    @patch("app.integrations.amap.httpx.get")
    def test_attraction_search_promotes_root_parent_before_sub_poi(
        self,
        mock_get,
    ):
        text_response = Mock()
        text_response.json.return_value = {
            "status": "1",
            "pois": [
                {
                    "id": "child",
                    "name": "陈家祠广场-古祠流芳",
                    "parent": "square",
                    "address": "陈家祠广场",
                    "cityname": "广州市",
                    "adname": "荔湾区",
                    "location": "113.2451,23.1260",
                    "type": "风景名胜;旅游景点",
                    "typecode": "110000",
                }
            ],
        }
        square_response = Mock()
        square_response.json.return_value = {
            "status": "1",
            "pois": [
                {
                    "id": "square",
                    "name": "陈家祠广场",
                    "parent": "main",
                    "address": "中山七路",
                    "cityname": "广州市",
                    "adname": "荔湾区",
                    "location": "113.2452,23.1261",
                    "type": "风景名胜;公园广场",
                    "typecode": "110105",
                }
            ],
        }
        main_response = Mock()
        main_response.json.return_value = {
            "status": "1",
            "pois": [
                {
                    "id": "main",
                    "name": "陈家祠堂",
                    "parent": "",
                    "address": "中山七路恩龙里34号",
                    "cityname": "广州市",
                    "adname": "荔湾区",
                    "location": "113.2451,23.1267",
                    "type": "风景名胜;国家级景点",
                    "typecode": "110202",
                }
            ],
        }
        mock_get.side_effect = [
            text_response,
            square_response,
            main_response,
        ]

        candidates = search_place_candidates(
            destination="广州",
            keywords="陈家祠",
            district="荔湾区",
            category="attraction",
            limit=3,
        )

        self.assertEqual(
            [candidate["amap_id"] for candidate in candidates],
            ["main", "square", "child"],
        )
        self.assertEqual(candidates[0]["selection_role"], "primary")
        self.assertEqual(candidates[1]["selection_role"], "sub_poi")
        self.assertEqual(mock_get.call_count, 3)

    @patch("app.integrations.amap.httpx.get")
    def test_exact_attraction_can_beat_its_container_parent(
        self,
        mock_get,
    ):
        text_response = Mock()
        text_response.json.return_value = {
            "status": "1",
            "pois": [
                {
                    "id": "tower",
                    "name": "广州塔",
                    "parent": "plaza",
                    "address": "阅江西路222号",
                    "cityname": "广州市",
                    "adname": "海珠区",
                    "location": "113.3245,23.1064",
                    "type": "风景名胜;国家级景点",
                    "typecode": "110202",
                }
            ],
        }
        parent_response = Mock()
        parent_response.json.return_value = {
            "status": "1",
            "pois": [
                {
                    "id": "plaza",
                    "name": "广州塔广场",
                    "parent": "",
                    "address": "广州塔路8号",
                    "cityname": "广州市",
                    "adname": "海珠区",
                    "location": "113.3245,23.1054",
                    "type": "风景名胜;公园广场",
                    "typecode": "110105",
                }
            ],
        }
        mock_get.side_effect = [text_response, parent_response]

        candidates = search_place_candidates(
            destination="广州",
            keywords="广州塔",
            district="海珠区",
            category="attraction",
            limit=2,
        )

        self.assertEqual(candidates[0]["amap_id"], "tower")
        self.assertEqual(candidates[0]["selection_role"], "primary")
        self.assertEqual(candidates[1]["amap_id"], "plaza")
        self.assertEqual(candidates[1]["selection_role"], "sub_poi")


if __name__ == "__main__":
    unittest.main()
