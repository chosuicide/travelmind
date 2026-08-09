<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute } from "vue-router";

import ChatSidebar from "../components/ChatSidebar.vue";
import ConversationPanel from "../components/ConversationPanel.vue";
import DestinationGate from "../components/DestinationGate.vue";
import ItineraryMapPanel from "../components/ItineraryMapPanel.vue";
import { api } from "../services/api.js";
import "../styles/chat-workspace.css";

const route = useRoute();

const conversations = ref([]);
const conversation = ref(null);
const provinces = ref([]);
const cities = ref([]);
const trip = ref(null);
const generationRun = ref(null);
const activeDayIndex = ref(0);
const busy = ref(false);
const deletingConversationId = ref(null);
const loading = ref(true);
const error = ref("");
const mobilePanel = ref("chat");
const sidebarOpen = ref(false);
const destinationSetup = ref(false);
const selectedProvinceCode = ref("");
const selectedCityCode = ref("");
let pollTimer = null;

const title = computed(() => {
  if (trip.value?.destination) return `${trip.value.destination.replace(/^.*省/, "")}行程`;
  const city = conversation.value?.draft?.city_name;
  return city ? `${city.replace(/市$/, "")}行程` : "规划新旅行";
});

const dayCount = computed(() => {
  if (trip.value?.days?.length) return trip.value.days.length;
  const start = conversation.value?.draft?.start_date;
  const end = conversation.value?.draft?.end_date;
  if (!start || !end) return null;
  return Math.round((new Date(end) - new Date(start)) / 86400000) + 1;
});

const generating = computed(() => conversation.value?.status === "generating");
const generated = computed(() => conversation.value?.status === "generated" && trip.value?.days?.length);

function uuid() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function refreshList() {
  const result = await api.listConversations();
  conversations.value = result.items;
}

async function loadCities(provinceCode) {
  if (!provinceCode) {
    cities.value = [];
    return;
  }
  const result = await api.listCities(provinceCode);
  cities.value = result.cities;
}

async function loadTrip() {
  if (!conversation.value?.trip_id) {
    trip.value = null;
    generationRun.value = null;
    return;
  }
  trip.value = await api.getTrip(conversation.value.trip_id);
  activeDayIndex.value = Math.min(activeDayIndex.value, Math.max((trip.value.days?.length || 1) - 1, 0));
}

async function loadGenerationRun() {
  if (!conversation.value?.trip_id) {
    generationRun.value = null;
    return;
  }
  try {
    generationRun.value = await api.getLatestRun(conversation.value.trip_id);
  } catch (requestError) {
    if (requestError.status === 404) {
      generationRun.value = null;
      return;
    }
    throw requestError;
  }
}

async function openConversation(id, { quiet = false } = {}) {
  if (!quiet) loading.value = true;
  error.value = "";
  try {
    conversation.value = await api.getConversation(id);
    destinationSetup.value = !conversation.value.draft?.city_code;
    selectedProvinceCode.value = conversation.value.draft?.province_code || "";
    selectedCityCode.value = conversation.value.draft?.city_code || "";
    await Promise.all([
      loadCities(conversation.value.draft?.province_code),
      loadTrip(),
    ]);
    await loadGenerationRun();
    sidebarOpen.value = false;
    schedulePoll();
  } catch (requestError) {
    error.value = requestError.message;
  } finally {
    loading.value = false;
  }
}

function beginNewConversation() {
  if (pollTimer) clearTimeout(pollTimer);
  conversation.value = null;
  trip.value = null;
  generationRun.value = null;
  cities.value = [];
  selectedProvinceCode.value = "";
  selectedCityCode.value = "";
  activeDayIndex.value = 0;
  destinationSetup.value = true;
  mobilePanel.value = "chat";
  sidebarOpen.value = false;
  error.value = "";
  loading.value = false;
}

async function selectDestinationProvince(code) {
  selectedProvinceCode.value = code;
  selectedCityCode.value = "";
  error.value = "";
  try {
    await loadCities(code);
    if (new Set(["110000", "120000", "310000", "500000"]).has(code)) {
      selectedCityCode.value = cities.value[0]?.code || "";
    }
  } catch (requestError) {
    cities.value = [];
    error.value = requestError.message;
  }
}

// === 模块：带合法目的地创建会话 ===
// 流程：读取省市选择 → 后端再次校验 → 一次创建完整草稿 → 开放 AI 输入
async function startDestinationConversation() {
  const province = provinces.value.find((item) => item.code === selectedProvinceCode.value);
  const city = cities.value.find((item) => item.code === selectedCityCode.value);
  if (!province || !city) return;

  busy.value = true;
  error.value = "";
  try {
    const destination = {
      province_code: province.code,
      province_name: province.name,
      city_code: city.code,
      city_name: city.name,
    };
    if (conversation.value && !conversation.value.draft?.city_code) {
      const result = await api.sendConversationMessage(conversation.value.id, {
        client_message_id: uuid(),
        content: `目的地：${province.name}${city.name}`,
        draft_patch: destination,
      });
      conversation.value = result.conversation;
    } else {
      conversation.value = await api.createConversation(destination);
    }
    trip.value = null;
    generationRun.value = null;
    activeDayIndex.value = 0;
    destinationSetup.value = false;
    await refreshList();
    mobilePanel.value = "chat";
    sidebarOpen.value = false;
  } catch (requestError) {
    error.value = requestError.message;
  } finally {
    busy.value = false;
    loading.value = false;
  }
}

// === 模块：侧栏删除对话 ===
// 流程：二次确认 → 删除对话资源 → 当前项切换/空列表新建 → 保留关联行程
async function deleteConversation(item) {
  if (!item || deletingConversationId.value !== null) return;
  const tripNote = item.trip_id ? "已生成的行程仍会保留。" : "";
  const confirmed = window.confirm(
    `确定删除“${item.destination || '新的旅行'}”对话吗？${tripNote}`,
  );
  if (!confirmed) return;

  deletingConversationId.value = item.id;
  busy.value = true;
  error.value = "";
  try {
    if (pollTimer) clearTimeout(pollTimer);
    await api.deleteConversation(item.id);
    conversations.value = conversations.value.filter(
      (conversationItem) => conversationItem.id !== item.id,
    );

    if (conversation.value?.id === item.id) {
      conversation.value = null;
      trip.value = null;
      generationRun.value = null;
      activeDayIndex.value = 0;
      if (conversations.value.length) {
        await openConversation(conversations.value[0].id);
      } else {
        beginNewConversation();
      }
    }
  } catch (requestError) {
    error.value = requestError.message;
  } finally {
    deletingConversationId.value = null;
    busy.value = false;
  }
}

async function sendPayload(content, extra = {}) {
  if (!conversation.value) return;
  busy.value = true;
  error.value = "";
  try {
    const result = await api.sendConversationMessage(conversation.value.id, {
      client_message_id: uuid(),
      content,
      ...extra,
    });
    conversation.value = result.conversation;
    await Promise.all([refreshList(), loadTrip()]);
    await loadGenerationRun();
    schedulePoll();
  } catch (requestError) {
    error.value = requestError.message;
  } finally {
    busy.value = false;
  }
}

async function resolveDraftPreview(messageId, action) {
  if (!conversation.value || !messageId) return;
  busy.value = true;
  error.value = "";
  try {
    conversation.value = action === "apply"
      ? await api.applyDraftPreview(conversation.value.id, messageId)
      : await api.dismissDraftPreview(conversation.value.id, messageId);
    await Promise.all([
      refreshList(),
      loadCities(conversation.value.draft?.province_code),
      loadTrip(),
    ]);
    await loadGenerationRun();
    schedulePoll();
  } catch (requestError) {
    error.value = requestError.message;
  } finally {
    busy.value = false;
  }
}

async function resolveProposal(proposalId, action) {
  if (!proposalId) return;
  busy.value = true;
  error.value = "";
  try {
    const result = action === "apply"
      ? await api.applyConversationProposal(conversation.value.id, proposalId)
      : await api.dismissConversationProposal(conversation.value.id, proposalId);
    trip.value = result.trip;
    conversation.value = await api.getConversation(conversation.value.id);
    await refreshList();
  } catch (requestError) {
    error.value = requestError.message;
  } finally {
    busy.value = false;
  }
}

// === 模块：生成状态轮询 ===
// 流程：确认需求 → 后台生成 → 定时读取会话/Trip → 成功后停止并展示地图
function schedulePoll() {
  if (pollTimer) clearTimeout(pollTimer);
  if (!generating.value || !conversation.value) return;
  pollTimer = setTimeout(async () => {
    await openConversation(conversation.value.id, { quiet: true });
  }, 2500);
}

async function initialize() {
  try {
    const [provinceResult, conversationResult] = await Promise.all([
      api.listProvinces(),
      api.listConversations(),
    ]);
    provinces.value = provinceResult.provinces;
    conversations.value = conversationResult.items;
    if (conversations.value.length) {
      const requestedTripId = Number(route.query.trip);
      const requestedConversation = conversations.value.find(
        (item) => item.trip_id === requestedTripId,
      );
      await openConversation(
        requestedConversation?.id || conversations.value[0].id,
      );
    } else {
      beginNewConversation();
    }
  } catch (requestError) {
    error.value = requestError.message;
    loading.value = false;
  }
}

onMounted(initialize);
onBeforeUnmount(() => pollTimer && clearTimeout(pollTimer));
</script>

<template>
  <main class="travelmind-workspace">
    <ChatSidebar
      :conversations="conversations"
      :active-id="conversation?.id"
      :deleting-id="deletingConversationId"
      :collapsed="!sidebarOpen"
      @new="beginNewConversation"
      @select="openConversation"
      @delete="deleteConversation"
      @close="sidebarOpen = false"
    />
    <button v-if="sidebarOpen" class="sidebar-scrim" type="button" aria-label="关闭侧栏" @click="sidebarOpen = false" />

    <section class="agent-workspace">
      <header class="workspace-header">
        <button class="menu-button" type="button" aria-label="打开对话列表" @click="sidebarOpen = true">
          <svg viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h16" /></svg>
        </button>
        <h1>{{ title }}</h1>
        <div class="validation-badges">
          <span :class="{ complete: generated }"><i>✓</i>{{ generated ? '地点已验证' : '地点待验证' }}</span>
          <span :class="{ complete: generated }"><i>✓</i>{{ generated ? '路线已规划' : generating ? '路线规划中' : '路线待规划' }}</span>
        </div>
        <span class="trip-duration">{{ dayCount ? `${dayCount} 天 ${Math.max(dayCount - 1, 0)} 晚` : '天数待定' }}<svg viewBox="0 0 24 24"><path d="m7 10 5 5 5-5" /></svg></span>
      </header>

      <nav class="mobile-view-switch" aria-label="切换工作区">
        <button type="button" :aria-selected="mobilePanel === 'chat'" @click="mobilePanel = 'chat'">对话</button>
        <button type="button" :aria-selected="mobilePanel === 'map'" @click="mobilePanel = 'map'">地图</button>
      </nav>

      <div class="workspace-body" :class="`show-${mobilePanel}`">
        <DestinationGate
          v-if="destinationSetup"
          :provinces="provinces"
          :cities="cities"
          :province-code="selectedProvinceCode"
          :city-code="selectedCityCode"
          :busy="busy || loading"
          :error="error"
          @select-province="selectDestinationProvince"
          @select-city="selectedCityCode = $event"
          @start="startDestinationConversation"
        />
        <ConversationPanel
          v-else
          :conversation="conversation"
          :busy="busy || loading"
          :error="error"
          @send="sendPayload"
          @apply-draft="resolveDraftPreview($event, 'apply')"
          @dismiss-draft="resolveDraftPreview($event, 'dismiss')"
          @apply="resolveProposal($event, 'apply')"
          @dismiss="resolveProposal($event, 'dismiss')"
        />
        <ItineraryMapPanel
          :trip="trip"
          :active-day-index="activeDayIndex"
          :generating="generating"
          :generation-run="generationRun"
          @select-day="activeDayIndex = $event"
        />
      </div>
    </section>
  </main>
</template>
