<script setup>
import { computed } from "vue";
import RouteMap from "./RouteMap.vue";

const props = defineProps({
  trip: { type: Object, default: null },
  activeDayIndex: { type: Number, default: 0 },
  generating: { type: Boolean, default: false },
  generationRun: { type: Object, default: null },
});
const emit = defineEmits(["select-day"]);

const activeDay = computed(() => props.trip?.days?.[props.activeDayIndex] || null);
const trace = computed(() => props.generationRun?.trace || []);
const runStatus = computed(() => props.generationRun?.status || (props.generating ? "queued" : null));

const progressEvents = computed(() => {
  const labels = {
    search_places: "查询真实地点",
    get_place_details: "核对地点详情",
    estimate_route: "计算地点间路线",
    check_itinerary: "检查完整行程",
  };
  return trace.value.slice(-4).map((event) => ({
    label: event.type === "quality"
      ? "检查节奏与路线"
      : event.type === "graph"
        ? { model: "Agent 分析下一步", tools: "执行旅行工具", validate: "验证行程结果" }[event.node] || "推进规划流程"
        : labels[event.name] || "整理行程信息",
    detail: event.candidate_count ? `找到 ${event.candidate_count} 个候选地点` : "已完成",
    status: event.status || "succeeded",
  }));
});

const progressPercent = computed(() => {
  if (runStatus.value === "succeeded") return 100;
  if (runStatus.value === "failed") return Math.min(92, 24 + trace.value.length * 10);
  if (runStatus.value === "running") return Math.min(92, 24 + trace.value.length * 10);
  return runStatus.value === "queued" ? 10 : 0;
});

const progressTitle = computed(() => {
  if (runStatus.value === "queued") return "行程已进入生成队列";
  if (runStatus.value === "failed") return "这次生成没有完成";
  const last = progressEvents.value.at(-1);
  return last ? last.label : "Agent 正在理解你的行程";
});

function activityName(activity) {
  return activity.verified_place?.name || activity.name;
}

function distanceLabel(value) {
  if (value == null) return "路线待计算";
  return value < 1000 ? `${value} 米` : `${(value / 1000).toFixed(1)} 公里`;
}

function legFor(index) {
  return activeDay.value?.route_legs?.[index] || null;
}
</script>

<template>
  <section class="itinerary-map-panel" aria-label="行程地图">
    <template v-if="activeDay">
      <RouteMap :day="activeDay" />
      <aside class="day-itinerary-card">
        <header>
          <span>第 {{ activeDay.day_number }} 天 · {{ activeDay.summary || "今日行程" }}</span>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 14 5-5 5 5" /></svg>
        </header>
        <ol>
          <li v-for="(activity, index) in activeDay.activities" :key="activity.id || index">
            <span class="stop-dot">{{ index + 1 }}</span>
            <div class="stop-copy">
              <strong>{{ activityName(activity) }}</strong>
              <time>{{ activity.start_time || "时间待定" }} – {{ activity.end_time || "待定" }}</time>
              <p>{{ activity.description || activity.verified_place?.address }}</p>
              <div v-if="legFor(index)" class="leg-meta">
                <span>⌁</span>
                {{ distanceLabel(legFor(index).distance_meters) }} · 约 {{ Math.round(legFor(index).duration_minutes || 0) }} 分钟
              </div>
            </div>
          </li>
        </ol>
        <div class="day-tabs" role="tablist" aria-label="选择日期">
          <button
            v-for="(day, index) in trip.days"
            :key="day.id"
            type="button"
            :aria-selected="index === activeDayIndex"
            @click="emit('select-day', index)"
          >{{ day.day_number }}</button>
        </div>
      </aside>
    </template>

    <div v-else class="map-empty-state" :class="{ 'is-generating': runStatus }">
      <div class="empty-map-lines" aria-hidden="true">
        <i /><i /><i /><i /><span>1</span><span>2</span><span>3</span>
      </div>

      <!-- === 模块：真实生成进度 === -->
      <!-- 流程：GenerationRun 状态 → 实时 trace 事件 → 地图进度面板 → 完成后切换真实路线 -->
      <section v-if="runStatus" class="generation-map-progress" aria-live="polite">
        <div class="progress-heading">
          <span class="agent-pulse"><i /></span>
          <div>
            <small>TravelMind Agent</small>
            <strong>{{ progressTitle }}</strong>
          </div>
          <b>{{ progressPercent }}%</b>
        </div>
        <div class="progress-track"><i :style="{ width: `${progressPercent}%` }" /></div>
        <ul v-if="progressEvents.length">
          <li v-for="(event, index) in progressEvents" :key="`${event.label}-${index}`">
            <span>{{ event.status === "failed" ? "!" : "✓" }}</span>
            <div><strong>{{ event.label }}</strong><small>{{ event.detail }}</small></div>
          </li>
        </ul>
        <p v-else-if="runStatus === 'queued'">Worker 将按提交顺序领取任务，请保持页面打开。</p>
        <p v-else-if="runStatus === 'failed'">{{ generationRun?.error_message || "可以检查需求后重新提交。" }}</p>
        <p v-else>正在读取需求并准备调用地点与路线工具…</p>
      </section>

      <div v-else class="empty-map-copy">
        <svg viewBox="0 0 42 34" aria-hidden="true"><path d="M4 28 15 10l8 13 5-8 10 13H4Z" /></svg>
        <strong>路线会在这里展开</strong>
        <p>先在左侧告诉 TravelMind 你想去哪里。</p>
      </div>
    </div>
  </section>
</template>
