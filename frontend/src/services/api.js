const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

export class ApiError extends Error {
  constructor(message, status, payload) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

// === 模块：统一后端请求入口 ===
// 流程：读取 JWT → 发送请求 → 解析响应 → 统一处理业务错误
export async function apiRequest(path, options = {}) {
  const token = localStorage.getItem("travelmind_token");
  const headers = new Headers(options.headers || {});

  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
  if (response.status === 204) return null;

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    if (response.status === 401) {
      localStorage.removeItem("travelmind_token");
      window.dispatchEvent(new CustomEvent("travelmind:unauthorized"));
    }
    const message =
      typeof payload === "object" && payload?.detail
        ? payload.detail
        : "请求失败，请稍后重试";
    throw new ApiError(message, response.status, payload);
  }
  return payload;
}

// === 模块：TravelMind API 语义层 ===
// 流程：页面动作 → 对应资源方法 → apiRequest → 返回结构化数据
export const api = {
  register: (body) => apiRequest("/auth/register", { method: "POST", body: JSON.stringify(body) }),
  login: (body) => apiRequest("/auth/login", { method: "POST", body: JSON.stringify(body) }),

  listProvinces: () => apiRequest("/regions"),
  listCities: (provinceCode) => apiRequest(`/regions/${provinceCode}/cities`),

  createConversation: (destination) => apiRequest("/conversations", {
    method: "POST",
    body: destination ? JSON.stringify(destination) : undefined,
  }),
  listConversations: (limit = 20, offset = 0) =>
    apiRequest(`/conversations?limit=${limit}&offset=${offset}`),
  getConversation: (conversationId) => apiRequest(`/conversations/${conversationId}`),
  deleteConversation: (conversationId) =>
    apiRequest(`/conversations/${conversationId}`, { method: "DELETE" }),
  sendConversationMessage: (conversationId, body) =>
    apiRequest(`/conversations/${conversationId}/messages`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  confirmConversation: (conversationId) =>
    apiRequest(`/conversations/${conversationId}/confirm`, { method: "POST" }),
  applyDraftPreview: (conversationId, messageId) =>
    apiRequest(`/conversations/${conversationId}/draft-previews/${messageId}/apply`, {
      method: "POST",
    }),
  dismissDraftPreview: (conversationId, messageId) =>
    apiRequest(`/conversations/${conversationId}/draft-previews/${messageId}/dismiss`, {
      method: "POST",
    }),
  applyConversationProposal: (conversationId, proposalId) =>
    apiRequest(`/conversations/${conversationId}/modification-proposals/${proposalId}/apply`, {
      method: "POST",
    }),
  dismissConversationProposal: (conversationId, proposalId) =>
    apiRequest(`/conversations/${conversationId}/modification-proposals/${proposalId}/dismiss`, {
      method: "POST",
    }),

  createTrip: (body) => apiRequest("/trips", { method: "POST", body: JSON.stringify(body) }),
  listTrips: () => apiRequest("/trips"),
  getTrip: (tripId) => apiRequest(`/trips/${tripId}`),
  deleteTrip: (tripId) => apiRequest(`/trips/${tripId}`, { method: "DELETE" }),
  generateTrip: (tripId) => apiRequest(`/trips/${tripId}/generate`, { method: "POST" }),
  getLatestRun: (tripId) => apiRequest(`/trips/${tripId}/generation-runs/latest`),
};
