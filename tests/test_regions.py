import unittest

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.conversations.schemas import TripDraft
from app.main import app


# === 全国地区目录测试：接口数据和对话草稿使用同一套精确校验 ===
# 流程：读取省份 → 查询广东城市 → 校验广州组合 → 拒绝伪造城市
class RegionCatalogTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()

    def test_region_api_returns_mainland_provinces_and_cities(self):
        provinces = self.client.get("/regions")
        cities = self.client.get("/regions/440000/cities")

        self.assertEqual(provinces.status_code, 200)
        self.assertEqual(len(provinces.json()["provinces"]), 31)
        self.assertIn(
            {"code": "440000", "name": "广东省"},
            provinces.json()["provinces"],
        )
        self.assertEqual(cities.status_code, 200)
        self.assertIn(
            {"code": "440100", "name": "广州市"},
            cities.json()["cities"],
        )

    def test_unknown_province_returns_not_found(self):
        response = self.client.get("/regions/990000/cities")
        self.assertEqual(response.status_code, 404)

    def test_draft_rejects_city_code_or_name_mismatch(self):
        valid = {
            "province_code": "440000",
            "province_name": "广东省",
            "city_code": "440100",
            "city_name": "广州市",
        }
        self.assertEqual(TripDraft.model_validate(valid).city_name, "广州市")

        with self.assertRaises(ValidationError):
            TripDraft.model_validate({**valid, "city_code": "449900"})
        with self.assertRaises(ValidationError):
            TripDraft.model_validate({**valid, "city_name": "深圳市"})


if __name__ == "__main__":
    unittest.main()
