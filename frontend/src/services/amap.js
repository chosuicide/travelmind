let amapPromise = null;

// === 模块：高德 JavaScript API 按需加载 ===
// 流程：读取前端 Key → 配置安全码 → 注入脚本 → 返回 window.AMap
export function loadAmap() {
  if (window.AMap) return Promise.resolve(window.AMap);
  if (amapPromise) return amapPromise;

  const key = import.meta.env.VITE_AMAP_JS_KEY;
  const securityJsCode = import.meta.env.VITE_AMAP_SECURITY_CODE;
  if (!key) {
    return Promise.reject(new Error("未配置地图 Key，当前显示路线预览图"));
  }
  if (securityJsCode) window._AMapSecurityConfig = { securityJsCode };

  amapPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(key)}`;
    script.async = true;
    script.onload = () => resolve(window.AMap);
    script.onerror = () => reject(new Error("高德地图加载失败，当前显示路线预览图"));
    document.head.appendChild(script);
  });
  return amapPromise;
}
