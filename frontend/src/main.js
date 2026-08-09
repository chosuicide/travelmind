import { createApp } from "vue";

import App from "./App.vue";
import router from "./router/index.js";
import "./styles/base.css";


// === 模块：Vue 应用入口 ===
// 流程：加载全局样式 → 注册路由 → 挂载 App
createApp(App).use(router).mount("#app");
