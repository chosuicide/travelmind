<script setup>
import { computed } from "vue";
import { useRouter } from "vue-router";

import { useAuth } from "../composables/useAuth.js";


const router = useRouter();
const { identity, logout } = useAuth();

const avatarLabel = computed(() => {
  const source = identity.value?.username || identity.value?.email || "T";
  return source.slice(0, 1).toUpperCase();
});

function handleLogout() {
  logout();
  router.push({ name: "login" });
}
</script>

<template>
  <header class="app-header">
    <RouterLink class="brand" :to="{ name: 'agent' }">TravelMind</RouterLink>
    <nav class="main-nav" aria-label="主导航">
      <RouterLink :to="{ name: 'agent' }">新行程</RouterLink>
      <RouterLink :to="{ name: 'trips' }">我的行程</RouterLink>
    </nav>
    <button class="account-button" type="button" aria-label="退出登录" @click="handleLogout">
      {{ avatarLabel }}
    </button>
  </header>
</template>
