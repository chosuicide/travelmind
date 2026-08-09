<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import AppHeader from "../components/AppHeader.vue";
import { api } from "../services/api.js";


const route = useRoute();
const router = useRouter();
const tripId = Number(route.params.tripId);

const run = ref(null);
const error = ref("");
const retrying = ref(false);
const elapsedSeconds = ref(0);
let pollTimer = null;
let clockTimer = null;

const agentStages = [
  { after: 0, title: "理解行程条件", detail: "读取城市、日期、预算和旅行偏好" },
  { after: 8, title: "查找真实地点", detail: "筛选可以被地图检索和验证的地点" },
  { after: 22, title: "组合每日安排", detail: "把相近地点放到同一天并调整顺序" },
  { after: 38, title: "检查距离与节奏", detail: "计算路线，避免一天安排得过满" },
  { after: 55, title: "整理最终行程", detail: "验证结构并保存活动与路线" },
];

const activeStageIndex = computed(() => {
  if (run.value?.status !== "running") return -1;
  return agentStages.reduce(
    (current, stage, index) => elapsedSeconds.value >= stage.after ? index : current,
    0,
  );
});

const queuedTooLong = computed(
  () => run.value?.status === "queued" && elapsedSeconds.value >= 15,
);

function updateElapsed() {
  const source = run.value?.started_at || run.value?.created_at;
  if (!source) return;
  const normalizedSource = /(?:Z|[+-]\d{2}:\d{2})$/.test(source)
    ? source
    : `${source}Z`;
  elapsedSeconds.value = Math.max(
    0,
    Math.floor((Date.now() - new Date(normalizedSource).getTime()) / 1000),
  );
}

async function finishGeneration() {
  const trip = await api.getTrip(tripId);
  if (trip.status !== "generated" || !trip.days?.length) {
    throw new Error("任务已结束，但完整行程尚未返回，请稍后刷新");
  }
  clearInterval(pollTimer);
  await router.replace({ name: "trip-detail", params: { tripId } });
}

// === 模块：生成任务轮询与结果交接 ===
// 流程：GET latest run → queued/running 动态阶段 → succeeded 后读取完整 Trip → 详情页
async function pollRun() {
  try {
    run.value = await api.getLatestRun(tripId);
    updateElapsed();
    error.value = "";
    if (run.value.status === "succeeded") await finishGeneration();
    if (run.value.status === "failed") clearInterval(pollTimer);
  } catch (requestError) {
    error.value = requestError.message;
  }
}

async function retry() {
  retrying.value = true;
  error.value = "";
  try {
    await api.generateTrip(tripId);
    await pollRun();
    clearInterval(pollTimer);
    pollTimer = setInterval(pollRun, 2000);
  } catch (requestError) {
    error.value = requestError.message;
  } finally {
    retrying.value = false;
  }
}

onMounted(async () => {
  await pollRun();
  if (!run.value || !["failed", "succeeded"].includes(run.value.status)) {
    pollTimer = setInterval(pollRun, 2000);
  }
  clockTimer = setInterval(updateElapsed, 1000);
});

onBeforeUnmount(() => {
  clearInterval(pollTimer);
  clearInterval(clockTimer);
});
</script>

<template>
  <div class="app-page">
    <AppHeader />
    <main id="main-content" class="generation-main content-shell">
      <section class="generation-copy">
        <p>TravelMind Agent</p>
        <h1 v-if="run?.status === 'failed'">这次规划没有完成</h1>
        <h1 v-else>正在为你整理行程</h1>
        <span v-if="run?.status !== 'failed'">
          页面展示的是任务状态和耗时阶段，完成后会自动进入行程详情。
        </span>
        <span v-else>{{ run.error_message || "生成失败，请重新尝试。" }}</span>
      </section>

      <section class="generation-process" aria-live="polite">
        <div class="queue-state" :class="{ complete: run?.status === 'running' }">
          <span class="step-mark">{{ run?.status === 'queued' ? '…' : '✓' }}</span>
          <div>
            <strong>任务进入生成队列</strong>
            <small>{{ run?.status === 'queued' ? '等待 Worker 领取任务' : 'Worker 已开始处理' }}</small>
          </div>
        </div>

        <ol class="generation-steps">
          <li
            v-for="(stage, index) in agentStages"
            :key="stage.title"
            :class="{
              active: index === activeStageIndex,
              complete: index < activeStageIndex || run?.status === 'succeeded',
            }"
          >
            <span class="step-mark">{{ index < activeStageIndex ? '✓' : index + 1 }}</span>
            <div>
              <strong>{{ stage.title }}</strong>
              <small>{{ stage.detail }}</small>
            </div>
          </li>
        </ol>

        <p v-if="queuedTooLong" class="worker-warning">
          任务仍在排队。开发环境中请确认 Generation Worker 已启动。
        </p>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <button
          v-if="run?.status === 'failed'"
          class="primary-action"
          :disabled="retrying"
          @click="retry"
        >
          {{ retrying ? "重新提交中…" : "重新开始规划" }}
        </button>
      </section>
    </main>
  </div>
</template>
