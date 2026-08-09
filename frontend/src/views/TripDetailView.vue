<script setup>
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import AppHeader from "../components/AppHeader.vue";
import RouteMap from "../components/RouteMap.vue";
import TripTimeline from "../components/TripTimeline.vue";
import { api } from "../services/api.js";


const route = useRoute();
const router = useRouter();
const tripId = Number(route.params.tripId);

const trip = ref(null);
const activeDayIndex = ref(0);
const loading = ref(true);
const error = ref("");
const deleting = ref(false);

const activeDay = computed(() => trip.value?.days?.[activeDayIndex.value]);

function paceLabel(pace) {
  return { relaxed: "轻松", balanced: "均衡", intensive: "充实" }[pace] || pace;
}

function distanceLabel(meters) {
  if (meters < 1000) return `${meters} 米`;
  return `${(meters / 1000).toFixed(1)} 公里`;
}

async function loadTrip() {
  loading.value = true;
  error.value = "";
  try {
    trip.value = await api.getTrip(tripId);
    if (trip.value.status === "generating") {
      await router.replace({ name: "generating", params: { tripId } });
    }
  } catch (requestError) {
    error.value = requestError.message;
  } finally {
    loading.value = false;
  }
}

// === 模块：行程详情删除 ===
// 流程：二次确认 → 删除当前 Trip → 返回行程列表
async function deleteCurrentTrip() {
  if (!trip.value || deleting.value) return;
  const confirmed = window.confirm(
    `确定删除“${trip.value.destination}”行程吗？删除后无法恢复。`,
  );
  if (!confirmed) return;

  deleting.value = true;
  error.value = "";
  try {
    await api.deleteTrip(trip.value.id);
    await router.replace({ name: "trips" });
  } catch (requestError) {
    error.value = requestError.message;
  } finally {
    deleting.value = false;
  }
}

onMounted(loadTrip);
</script>

<template>
  <div class="app-page trip-detail-page">
    <AppHeader />
    <main id="main-content" class="trip-workspace">
      <p v-if="loading" class="page-message">正在读取行程…</p>
      <p v-else-if="error && !trip" class="page-message form-error">{{ error }}</p>

      <template v-else-if="trip">
        <aside class="trip-overview-panel">
          <RouterLink class="back-link" :to="{ name: 'trips' }">← 返回我的行程</RouterLink>
          <div class="trip-identity">
            <h1>{{ trip.destination }}</h1>
            <p>{{ trip.start_date }} — {{ trip.end_date }}</p>
            <span>{{ trip.people }} 人 · ¥{{ trip.budget }} · {{ paceLabel(trip.pace) }}</span>
          </div>

          <nav class="trip-day-index" aria-label="行程日期概览">
            <button
              v-for="(day, index) in trip.days"
              :key="day.id"
              type="button"
              :aria-current="activeDayIndex === index ? 'step' : undefined"
              @click="activeDayIndex = index"
            >
              <span>第 {{ day.day_number }} 天</span>
              <strong>{{ day.summary }}</strong>
            </button>
          </nav>

          <RouterLink
            class="primary-action trip-chat-link"
            :to="{ name: 'agent', query: { trip: trip.id } }"
          >
            回到规划对话
          </RouterLink>

          <button
            type="button"
            class="trip-detail-delete"
            :disabled="deleting"
            @click="deleteCurrentTrip"
          >{{ deleting ? '正在删除…' : '删除此行程' }}</button>

          <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        </aside>

        <section class="map-workspace" aria-label="地图和每日行程">
          <header class="map-toolbar">
            <div class="day-switcher" role="tablist" aria-label="选择行程日期">
              <button
                v-for="(day, index) in trip.days"
                :key="day.id"
                type="button"
                :aria-selected="activeDayIndex === index"
                @click="activeDayIndex = index"
              >
                第 {{ day.day_number }} 天
              </button>
            </div>
            <div v-if="activeDay" class="map-day-summary">
              <strong>{{ activeDay.summary }}</strong>
              <span v-if="activeDay.route_summary?.is_complete">
                {{ distanceLabel(activeDay.route_summary.total_distance_meters) }}
                · 约{{ activeDay.route_summary.total_duration_minutes }}分钟
              </span>
              <span v-else>部分地点缺少路线数据</span>
            </div>
          </header>

          <RouteMap v-if="activeDay" :day="activeDay" />

          <footer v-if="activeDay" class="itinerary-strip">
            <TripTimeline :day="activeDay" />
          </footer>
        </section>
      </template>
    </main>
  </div>
</template>
