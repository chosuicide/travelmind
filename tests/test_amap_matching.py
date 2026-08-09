import unittest
from unittest.mock import Mock, patch

from app.integrations.amap import (
    search_place,
    select_best_place_candidate,
)


def _candidate(
    poi_id: str,
    name: str,
    address: str,
    district: str,
    poi_type: str,
    typecode: str,
    coordinates: str = "113.2644,23.1291",
) -> dict:
    return {
        "id": poi_id,
        "name": name,
        "address": address,
        "adname": district,
        "cityname": "广州市",
        "type": poi_type,
        "typecode": typecode,
        "location": coordinates,
    }


# === 高德候选匹配测试：复现真实错配并验证正确候选或拒绝结果 ===
# 流程：模拟高德候选 → 本地评分 → 选择可信 POI / 拒绝错误 POI
class AMapPlaceMatchingTests(unittest.TestCase):
    def test_attraction_beats_same_name_subway_station(self):
        candidates = [
            _candidate(
                "subway",
                "陈家祠(地铁站)",
                "1号线;8号线",
                "荔湾区",
                "交通设施服务;地铁站;地铁站",
                "150500",
            ),
            _candidate(
                "attraction",
                "陈家祠堂",
                "中山七路恩龙里34号",
                "荔湾区",
                "风景名胜;风景名胜;国家级景点",
                "110202",
            ),
        ]

        result = select_best_place_candidate(
            name="陈家祠",
            location=(
                "广州市荔湾区中山七路恩龙里34号"
                "（地铁陈家祠站D出口步行110米）"
            ),
            candidates=candidates,
        )

        self.assertEqual(result["id"], "attraction")

    def test_wrong_restaurant_branch_is_rejected(self):
        candidates = [
            _candidate(
                "changgang",
                "点都德(昌岗店)",
                "昌岗中路238号",
                "海珠区",
                "餐饮服务;中餐厅;中餐厅",
                "050100",
            ),
            _candidate(
                "huacheng",
                "点都德(花城店)",
                "华穗路217号",
                "天河区",
                "餐饮服务;中餐厅;中餐厅",
                "050100",
            ),
        ]

        result = select_best_place_candidate(
            name="点都德（长提店）",
            location="广州市荔湾区长堤大马路318号",
            candidates=candidates,
        )

        self.assertIsNone(result)

    def test_requested_pier_beats_first_search_result(self):
        candidates = [
            _candidate(
                "dashatou",
                "珠江夜游(大沙头码头)",
                "沿江东路466号大沙头游船码头内",
                "越秀区",
                "风景名胜;旅游景点",
                "110200",
            ),
            _candidate(
                "tianzi",
                "珠江夜游(天字码头)",
                "沿江中路200号",
                "越秀区",
                "风景名胜;旅游景点",
                "110000",
            ),
        ]

        result = select_best_place_candidate(
            name="珠江夜游",
            location="天字码头（越秀区沿江中路200号）",
            candidates=candidates,
        )

        self.assertEqual(result["id"], "tianzi")

    def test_primary_memorial_beats_named_sub_attraction(self):
        candidates = [
            _candidate(
                "memorial",
                "中山纪念堂",
                "东风中路299号",
                "越秀区",
                "风景名胜;纪念馆;国家级景点",
                "110204|110202",
            ),
            _candidate(
                "tree",
                "中山纪念堂-树上树",
                "东风中路259号中山纪念堂(东南角)",
                "越秀区",
                "风景名胜;旅游景点",
                "110000",
            ),
        ]

        result = select_best_place_candidate(
            name="中山纪念堂",
            location="越秀区东风中路259号",
            candidates=candidates,
        )

        self.assertEqual(result["id"], "memorial")

    @patch("app.integrations.amap.httpx.get")
    def test_search_place_fetches_ten_candidates_and_serializes_winner(
        self,
        mock_get,
    ):
        response = Mock()
        response.json.return_value = {
            "status": "1",
            "pois": [
                _candidate(
                    "park",
                    "越秀公园",
                    "解放北路988号",
                    "越秀区",
                    "风景名胜;公园广场;公园",
                    "110101",
                    "113.265561,23.140096",
                )
            ],
        }
        mock_get.return_value = response

        result = search_place(
            name="越秀公园",
            location="广州市越秀区解放北路988号",
            destination="广州",
        )

        self.assertEqual(result["amap_id"], "park")
        self.assertEqual(result["latitude"], 23.140096)
        self.assertEqual(result["longitude"], 113.265561)
        self.assertEqual(
            mock_get.call_args.kwargs["params"]["page_size"],
            10,
        )
        response.raise_for_status.assert_called_once()


if __name__ == "__main__":
    unittest.main()
