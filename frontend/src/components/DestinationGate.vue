<script setup>
defineProps({
  provinces: { type: Array, default: () => [] },
  cities: { type: Array, default: () => [] },
  provinceCode: { type: String, default: "" },
  cityCode: { type: String, default: "" },
  busy: { type: Boolean, default: false },
  error: { type: String, default: "" },
});

const emit = defineEmits(["select-province", "select-city", "start"]);
</script>

<template>
  <section class="destination-gate" aria-labelledby="destination-gate-title">
    <div class="destination-gate-card">
      <div class="destination-gate-mark" aria-hidden="true">
        <svg viewBox="0 0 28 28"><path d="M3 23 11 8l5 10 4-7 6 12H3Z" /></svg>
      </div>
      <p class="destination-gate-kicker">新行程</p>
      <h2 id="destination-gate-title">先确定目的地</h2>
      <p class="destination-gate-copy">
        省市由系统确认，地图和地点检索才不会跑偏。选好后，再把其余想法自然地告诉 TravelMind。
      </p>

      <!-- === 模块：聊天前省市门槛 === -->
      <!-- 流程：选择省份 → 加载合法城市 → 确认目的地 → 创建 Agent 会话 -->
      <div class="destination-fields">
        <label>
          <span><b>01</b> 省份</span>
          <select
            :value="provinceCode"
            :disabled="busy"
            @change="emit('select-province', $event.target.value)"
          >
            <option value="" disabled>请选择省份</option>
            <option v-for="province in provinces" :key="province.code" :value="province.code">
              {{ province.name }}
            </option>
          </select>
        </label>

        <label>
          <span><b>02</b> 城市</span>
          <select
            :value="cityCode"
            :disabled="busy || !provinceCode"
            @change="emit('select-city', $event.target.value)"
          >
            <option value="" disabled>{{ provinceCode ? '请选择城市' : '请先选择省份' }}</option>
            <option v-for="city in cities" :key="city.code" :value="city.code">
              {{ city.name }}
            </option>
          </select>
        </label>
      </div>

      <button
        class="destination-start-button"
        type="button"
        :disabled="busy || !provinceCode || !cityCode"
        @click="emit('start')"
      >
        {{ busy ? '正在创建…' : '开始规划' }}
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 7 7-7 7" /></svg>
      </button>
      <p v-if="error" class="destination-gate-error" role="alert">{{ error }}</p>
    </div>
  </section>
</template>
