import cityData from "@province-city-china/city";
import provinceData from "@province-city-china/province";


const MUNICIPALITY_CODES = new Set(["11", "12", "31", "50"]);


// === 模块：大陆省市级联数据 ===
// 流程：行政区划包 → 排除港澳台 → 省份筛选城市 → 拼成后端 destination
export const mainlandProvinces = provinceData.filter(({ province }) =>
  Number(province) >= 11 && Number(province) <= 65,
);


export function citiesForProvince(provinceCode) {
  const provincePrefix = String(provinceCode).slice(0, 2);
  const province = mainlandProvinces.find(
    (item) => item.province === provincePrefix,
  );
  if (!province) return [];

  if (MUNICIPALITY_CODES.has(provincePrefix)) {
    return [{ ...province, city: "00" }];
  }

  return cityData.filter((item) => item.province === provincePrefix);
}


export function formatDestination(provinceCode, cityCode) {
  const province = mainlandProvinces.find(
    (item) => item.code === provinceCode,
  );
  const city = citiesForProvince(provinceCode).find(
    (item) => item.code === cityCode,
  );
  if (!province || !city) return "";
  if (province.name === city.name) return city.name;
  return `${province.name}${city.name}`;
}
