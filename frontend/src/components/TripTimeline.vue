<script setup>
import { computed } from "vue";


const props = defineProps({
  day: {
    type: Object,
    required: true,
  },
});

const legsByOrigin = computed(() =>
  Object.fromEntries(
    (props.day.route_legs || []).map((leg) => [leg.origin_activity_id, leg]),
  ),
);

function modeLabel(mode) {
  return { walking: "步行", driving: "驾车", transit: "公交" }[mode] || mode;
}

function distanceLabel(meters) {
  if (meters < 1000) return `${meters} 米`;
  return `${(meters / 1000).toFixed(1)} 公里`;
}
</script>

<template>
  <div class="route-stops">
    <article v-for="(activity, index) in day.activities" :key="activity.id" class="route-stop">
      <span class="stop-number">{{ index + 1 }}</span>
      <div>
        <time>{{ activity.start_time || "待定" }}</time>
        <strong>{{ activity.verified_place?.name || activity.name }}</strong>
        <small v-if="legsByOrigin[activity.id]">
          {{ modeLabel(legsByOrigin[activity.id].mode) }}
          {{ distanceLabel(legsByOrigin[activity.id].distance_meters) }}
          · {{ legsByOrigin[activity.id].duration_minutes }}分钟
        </small>
        <small v-else>{{ activity.verified_place?.address || activity.location }}</small>
      </div>
    </article>
  </div>
</template>
