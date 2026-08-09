import { computed, ref } from "vue";

import { api } from "../services/api.js";


const token = ref(localStorage.getItem("travelmind_token"));
const identity = ref(
  JSON.parse(localStorage.getItem("travelmind_identity") || "null"),
);


function persistIdentity(value) {
  identity.value = value;
  if (value) {
    localStorage.setItem("travelmind_identity", JSON.stringify(value));
  } else {
    localStorage.removeItem("travelmind_identity");
  }
}


// === 模块：登录状态管理 ===
// 流程：注册/登录 → 保存 JWT → 路由放行；退出/401 → 清理本地状态
export function useAuth() {
  const isAuthenticated = computed(() => Boolean(token.value));

  async function login(email, password) {
    const result = await api.login({ email, password });
    token.value = result.access_token;
    localStorage.setItem("travelmind_token", result.access_token);
    persistIdentity({ email });
  }

  async function register(username, email, password) {
    await api.register({ username, email, password });
    await login(email, password);
    persistIdentity({ username, email });
  }

  function logout() {
    token.value = null;
    localStorage.removeItem("travelmind_token");
    persistIdentity(null);
  }

  return {
    identity,
    isAuthenticated,
    login,
    register,
    logout,
  };
}
