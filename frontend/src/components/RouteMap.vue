<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { loadAmap } from "../services/amap.js";

const props = defineProps({ day: { type: Object, required: true } });
const mapElement = ref(null);
const mapError = ref("");
let map = null;

const activities = computed(() =>
  (props.day.activities || []).filter((item) => item.verified_place?.longitude != null && item.verified_place?.latitude != null),
);
const routePoints = computed(() =>
  (props.day.route_legs || []).flatMap((leg) => leg.polyline || []).filter((point) => Array.isArray(point) && point.length === 2),
);
const activityPoints = computed(() => activities.value.map((item) => [item.verified_place.longitude, item.verified_place.latitude]));

const fallbackGeometry = computed(() => {
  const source = routePoints.value.length ? routePoints.value : activityPoints.value;
  if (!source.length) return { route: [], markers: [] };
  const xs = source.map((point) => point[0]);
  const ys = source.map((point) => point[1]);
  const minX = Math.min(...xs); const maxX = Math.max(...xs);
  const minY = Math.min(...ys); const maxY = Math.max(...ys);
  const normalize = ([x, y]) => [12 + ((x - minX) / (maxX - minX || 1)) * 64, 86 - ((y - minY) / (maxY - minY || 1)) * 70];
  return {
    route: (routePoints.value.length ? routePoints.value : activityPoints.value).map(normalize),
    markers: activityPoints.value.map(normalize),
  };
});
const fallbackPath = computed(() => fallbackGeometry.value.route.map((point, index) => `${index ? "L" : "M"} ${point[0]} ${point[1]}`).join(" "));

function markerContent(activity, index) {
  const name = activity.verified_place?.name || activity.name;
  return `<div class="amap-place-marker"><span>${index + 1}</span><strong>${name}</strong></div>`;
}

async function renderMap() {
  if (!mapElement.value) return;
  try {
    const AMap = await loadAmap();
    mapError.value = "";
    if (!map) {
      map = new AMap.Map(mapElement.value, { zoom: 12, mapStyle: "amap://styles/whitesmoke", viewMode: "2D" });
    } else map.clearMap();

    const overlays = [];
    activities.value.forEach((activity, index) => {
      const marker = new AMap.Marker({
        position: [activity.verified_place.longitude, activity.verified_place.latitude],
        content: markerContent(activity, index),
        offset: new AMap.Pixel(-18, -18),
        title: activity.verified_place.name,
      });
      map.add(marker); overlays.push(marker);
    });
    (props.day.route_legs || []).forEach((leg) => {
      if (!leg.polyline?.length) return;
      const route = new AMap.Polyline({
        path: leg.polyline,
        strokeColor: "#ef8b2c",
        borderWeight: 3,
        strokeWeight: 6,
        strokeOpacity: 1,
        lineJoin: "round",
        lineCap: "round",
      });
      map.add(route); overlays.push(route);
    });
    if (overlays.length) map.setFitView(overlays, false, [80, 300, 80, 80]);
  } catch (requestError) {
    mapError.value = requestError.message;
  }
}

onMounted(renderMap);
watch(() => props.day, async () => { await nextTick(); await renderMap(); }, { deep: true });
onBeforeUnmount(() => map?.destroy());
</script>

<template>
  <div class="route-map-shell">
    <div ref="mapElement" class="route-map" />
    <div v-if="mapError" class="map-fallback">
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        <g class="fallback-water"><path d="M70 -10C58 20 82 30 72 55S55 83 65 110" /></g>
        <g class="fallback-streets"><path d="M0 18 100 35M0 72 100 58M18 0 33 100M50 0 42 100M87 0 60 100M0 43 100 82" /></g>
        <path v-if="fallbackPath" class="fallback-route-casing" :d="fallbackPath" />
        <path v-if="fallbackPath" class="fallback-route" :d="fallbackPath" />
        <g class="fallback-markers">
          <g v-for="(point, index) in fallbackGeometry.markers" :key="index">
            <circle :cx="point[0]" :cy="point[1]" r="3.8" />
            <text :x="point[0]" :y="point[1] + 1.2">{{ index + 1 }}</text>
          </g>
        </g>
      </svg>
      <span class="map-key-note">{{ mapError }}</span>
    </div>
  </div>
</template>
