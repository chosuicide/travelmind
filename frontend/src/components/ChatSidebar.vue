<script setup>
import { useRouter } from "vue-router";
import { useAuth } from "../composables/useAuth.js";

defineProps({
  conversations: { type: Array, default: () => [] },
  activeId: { type: Number, default: null },
  deletingId: { type: Number, default: null },
  collapsed: { type: Boolean, default: false },
});
const emit = defineEmits(["new", "select", "delete", "close"]);

const router = useRouter();
const { logout } = useAuth();

function placeLabel(item) {
  return item.destination || "新的旅行";
}

function timeLabel(value) {
  if (!value) return "刚刚";
  const date = new Date(value);
  const today = new Date();
  if (date.toDateString() === today.toDateString()) {
    return `今天 ${date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`;
  }
  return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

function signOut() {
  logout();
  router.push({ name: "login" });
}
</script>

<template>
  <aside class="chat-sidebar" :class="{ 'is-open': !collapsed }" aria-label="对话导航">
    <div class="brand-lockup">
      <svg viewBox="0 0 42 34" aria-hidden="true">
        <path d="M4 28 15 10l8 13 5-8 10 13H4Z" />
        <circle cx="28" cy="7" r="4" />
      </svg>
      <strong>TravelMind</strong>
    </div>

    <button class="new-chat-button" type="button" @click="emit('new')">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
      新建对话
    </button>

    <section class="recent-section">
      <p>最近对话</p>
      <div
        v-for="item in conversations"
        :key="item.id"
        class="conversation-row-shell"
        :class="{ active: item.id === activeId }"
      >
        <button
          class="conversation-row"
          type="button"
          @click="emit('select', item.id)"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M20 10c0 5-8 11-8 11S4 15 4 10a8 8 0 1 1 16 0Z" />
            <circle cx="12" cy="10" r="2.5" />
          </svg>
          <span>
            <strong>{{ placeLabel(item) }}</strong>
            <small>{{ timeLabel(item.updated_at) }}</small>
          </span>
        </button>
        <button
          class="conversation-delete"
          type="button"
          :disabled="deletingId !== null"
          :aria-label="`删除${placeLabel(item)}对话`"
          @click="emit('delete', item)"
        >{{ deletingId === item.id ? '删除中' : '删除' }}</button>
      </div>
      <p v-if="!conversations.length" class="empty-conversations">还没有对话</p>
    </section>

    <nav class="sidebar-footer">
      <RouterLink :to="{ name: 'trips' }">
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="7" r="4" /><path d="M4 21v-2a7 7 0 0 1 7-7h2a7 7 0 0 1 7 7v2H4Z" /></svg>
        我的行程
      </RouterLink>
      <button type="button" @click="signOut">
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9 7 7M17 17l2.1 2.1M19.1 4.9 17 7M7 17l-2.1 2.1" /></svg>
        退出登录
      </button>
    </nav>
  </aside>
</template>
