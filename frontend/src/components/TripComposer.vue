<script setup>
import { computed } from "vue";

import { citiesForProvince, mainlandProvinces } from "../data/regions.js";


const props = defineProps({
  modelValue: {
    type: Object,
    required: true,
  },
  loading: Boolean,
  error: {
    type: String,
    default: "",
  },
});

const emit = defineEmits(["update:modelValue", "submit"]);

const interestOptions = ["人文", "美食", "自然", "摄影", "建筑", "艺术", "亲子"];
const cityOptions = computed(() => citiesForProvince(props.modelValue.province_code));

function update(field, value) {
  emit("update:modelValue", {
    ...props.modelValue,
    [field]: value,
  });
}

function updateProvince(provinceCode) {
  const cities = citiesForProvince(provinceCode);
  emit("update:modelValue", {
    ...props.modelValue,
    province_code: provinceCode,
    city_code: cities[0]?.code || "",
  });
}

function toggleInterest(interest) {
  const selected = props.modelValue.interests;
  if (selected.includes(interest)) {
    if (selected.length === 1) return;
    update("interests", selected.filter((item) => item !== interest));
    return;
  }
  update("interests", [...selected, interest]);
}
</script>

<template>
  <form class="planning-form" @submit.prevent="emit('submit')">
    <div class="form-section destination-section">
      <div class="form-section-heading">
        <span>01</span>
        <div>
          <h2>选择目的地</h2>
          <p>目前支持中国大陆省、市两级选择。</p>
        </div>
      </div>
      <div class="destination-selects">
        <label class="form-field">
          <span>省份</span>
          <select
            :value="modelValue.province_code"
            required
            @change="updateProvince($event.target.value)"
          >
            <option
              v-for="province in mainlandProvinces"
              :key="province.code"
              :value="province.code"
            >
              {{ province.name }}
            </option>
          </select>
        </label>
        <label class="form-field">
          <span>城市</span>
          <select
            :value="modelValue.city_code"
            required
            @change="update('city_code', $event.target.value)"
          >
            <option v-for="city in cityOptions" :key="city.code" :value="city.code">
              {{ city.name }}
            </option>
          </select>
        </label>
      </div>
    </div>

    <div class="form-section">
      <div class="form-section-heading">
        <span>02</span>
        <div>
          <h2>行程条件</h2>
          <p>最长支持 5 天，Agent 会根据人数和预算控制安排。</p>
        </div>
      </div>
      <div class="condition-grid">
        <label class="form-field">
          <span>出发日期</span>
          <input
            type="date"
            :value="modelValue.start_date"
            required
            @input="update('start_date', $event.target.value)"
          />
        </label>
        <label class="form-field">
          <span>结束日期</span>
          <input
            type="date"
            :min="modelValue.start_date"
            :value="modelValue.end_date"
            required
            @input="update('end_date', $event.target.value)"
          />
        </label>
        <label class="form-field">
          <span>人数</span>
          <select
            :value="modelValue.people"
            @change="update('people', Number($event.target.value))"
          >
            <option v-for="count in 8" :key="count" :value="count">{{ count }} 人</option>
          </select>
        </label>
        <label class="form-field">
          <span>总预算</span>
          <div class="money-input">
            <span>¥</span>
            <input
              type="number"
              :value="modelValue.budget"
              min="1"
              max="1000000"
              required
              @input="update('budget', Number($event.target.value))"
            />
          </div>
        </label>
        <label class="form-field">
          <span>节奏</span>
          <select :value="modelValue.pace" @change="update('pace', $event.target.value)">
            <option value="relaxed">轻松</option>
            <option value="balanced">均衡</option>
            <option value="intensive">充实</option>
          </select>
        </label>
      </div>
    </div>

    <div class="form-section">
      <div class="form-section-heading">
        <span>03</span>
        <div>
          <h2>告诉 Agent 你的偏好</h2>
          <p>这些信息会直接进入生成提示词。</p>
        </div>
      </div>
      <div class="interest-list" aria-label="选择兴趣">
        <button
          v-for="interest in interestOptions"
          :key="interest"
          type="button"
          :aria-pressed="modelValue.interests.includes(interest)"
          @click="toggleInterest(interest)"
        >
          {{ interest }}
        </button>
      </div>
      <label class="form-field notes-input">
        <span>补充说明</span>
        <textarea
          :value="modelValue.notes"
          rows="4"
          maxlength="1000"
          placeholder="例如：第一次来，希望每天不要太赶，想多体验本地餐馆。"
          @input="update('notes', $event.target.value)"
        />
      </label>
    </div>

    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <button class="primary-action planning-submit" :disabled="loading">
      {{ loading ? "正在创建行程…" : "让 Agent 开始规划" }}
    </button>
  </form>
</template>
