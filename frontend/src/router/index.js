import { createRouter, createWebHistory } from "vue-router";

import AgentHomeView from "../views/AgentHomeView.vue";
import AuthView from "../views/AuthView.vue";
import GenerationView from "../views/GenerationView.vue";
import TripDetailView from "../views/TripDetailView.vue";
import TripsView from "../views/TripsView.vue";


const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/login", name: "login", component: AuthView },
    {
      path: "/",
      name: "agent",
      component: AgentHomeView,
      meta: { requiresAuth: true },
    },
    {
      path: "/trips",
      name: "trips",
      component: TripsView,
      meta: { requiresAuth: true },
    },
    {
      path: "/trips/:tripId/generating",
      name: "generating",
      component: GenerationView,
      meta: { requiresAuth: true },
    },
    {
      path: "/trips/:tripId",
      name: "trip-detail",
      component: TripDetailView,
      meta: { requiresAuth: true },
    },
  ],
});


// === 模块：页面访问边界 ===
// 流程：检查本地 JWT → 未登录转登录页 → 已登录进入 Agent 工作区
router.beforeEach((to) => {
  const hasToken = Boolean(localStorage.getItem("travelmind_token"));
  if (to.meta.requiresAuth && !hasToken) {
    return { name: "login", query: { redirect: to.fullPath } };
  }
  if (to.name === "login" && hasToken) {
    return { name: "agent" };
  }
  return true;
});


window.addEventListener("travelmind:unauthorized", () => {
  if (router.currentRoute.value.name !== "login") {
    router.push({ name: "login" });
  }
});


export default router;
