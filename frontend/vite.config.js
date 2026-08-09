import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";


// === 模块：前端开发服务器与后端代理 ===
// 流程：浏览器 /api → Vite Proxy → FastAPI 8000 → 返回真实业务数据
export default defineConfig({
  plugins: [vue()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
