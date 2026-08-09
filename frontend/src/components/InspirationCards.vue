<script setup>
defineProps({
  items: {
    type: Array,
    required: true,
  },
});

defineEmits(["select"]);
</script>

<template>
  <section class="recommendation-section" aria-labelledby="recommendation-title">
    <div class="recommendation-heading">
      <div>
        <h2 id="recommendation-title">不知道去哪儿？</h2>
        <p>选择一个推荐方案，再按自己的需要调整。</p>
      </div>
    </div>
    <div class="recommendation-list">
      <button
        v-for="item in items"
        :key="item.title"
        type="button"
        class="recommendation-item"
        @click="$emit('select', item)"
      >
        <span class="recommendation-index">{{ item.index }}</span>
        <span class="recommendation-copy">
          <strong>{{ item.title }}</strong>
          <small>{{ item.meta }}</small>
        </span>
        <span class="recommendation-city">{{ item.city }}</span>
      </button>
    </div>
  </section>
</template>
