<script setup>
import { onMounted, ref } from "vue";

import AppHeader from "../components/AppHeader.vue";
import { api } from "../services/api.js";


const trips = ref([]);
const loading = ref(true);
const error = ref("");
const deletingId = ref(null);

function statusLabel(status) {
  return {
    created: "待生成",
    generating: "规划中",
    generation_failed: "生成失败",
  }[status] || status;
}

async function loadTrips() {
  loading.value = true;
  try {
    trips.value = await api.listTrips();
  } catch (requestError) {
    error.value = requestError.message;
  } finally {
    loading.value = false;
  }
}

// === 模块：行程列表删除 ===
// 流程：用户点击 → 二次确认 → DELETE 接口 → 本地移除列表项
async function deleteTrip(trip) {
  if (deletingId.value !== null) return;
  const confirmed = window.confirm(
    `确定删除“${trip.destination}”行程吗？删除后无法恢复。`,
  );
  if (!confirmed) return;

  deletingId.value = trip.id;
  error.value = "";
  try {
    await api.deleteTrip(trip.id);
    trips.value = trips.value.filter((item) => item.id !== trip.id);
  } catch (requestError) {
    error.value = requestError.message;
  } finally {
    deletingId.value = null;
  }
}

onMounted(loadTrips);
</script>

<template>
  <div class="app-page">
    <AppHeader />
    <main class="trips-main page-shell">
      <div class="page-title-row">
        <div>
          <p>保存的行程</p>
          <h1>我的行程</h1>
        </div>
        <RouterLink class="primary-action" :to="{ name: 'agent' }">
          新建行程
        </RouterLink>
      </div>

      <p v-if="loading" class="empty-state">正在读取行程…</p>
      <p v-else-if="error" class="form-error">{{ error }}</p>
      <div v-else-if="trips.length" class="trips-list">
        <article
          v-for="trip in trips"
          :key="trip.id"
          class="trip-row"
        >
          <RouterLink
            class="trip-row-link"
            :to="
              trip.status === 'generating'
                ? { name: 'generating', params: { tripId: trip.id } }
                : { name: 'trip-detail', params: { tripId: trip.id } }
            "
          >
            <strong>{{ trip.destination }}</strong>
            <span>{{ trip.start_date }} - {{ trip.end_date }}</span>
            <span>{{ trip.people }}人 · ¥{{ trip.budget }}</span>
            <small v-if="trip.status !== 'generated'" :class="`status-${trip.status}`">{{ statusLabel(trip.status) }}</small>
            <i aria-hidden="true">→</i>
          </RouterLink>
          <button
            type="button"
            class="trip-delete-button"
            :disabled="deletingId !== null"
            :aria-label="`删除${trip.destination}行程`"
            @click="deleteTrip(trip)"
          >{{ deletingId === trip.id ? '删除中' : '删除' }}</button>
        </article>
      </div>
      <div v-else class="empty-state">
        <h2>还没有行程</h2>
        <p>把第一个旅行想法交给 Agent。</p>
      </div>
    </main>
  </div>
</template>
