<script setup>
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import guilinHero from "../assets/guilin-login-hero.png";
import { useAuth } from "../composables/useAuth.js";

const route = useRoute();
const router = useRouter();
const { login, register } = useAuth();

const mode = ref("login");
const username = ref("");
const email = ref("");
const password = ref("");
const loading = ref(false);
const error = ref("");
const isRegister = computed(() => mode.value === "register");

async function submit() {
  loading.value = true;
  error.value = "";
  try {
    if (isRegister.value) {
      await register(username.value, email.value, password.value);
    } else {
      await login(email.value, password.value);
    }
    await router.push(route.query.redirect || { name: "agent" });
  } catch (requestError) {
    error.value = requestError.message;
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <main class="auth-page">
    <section
      class="auth-visual"
      :style="{ backgroundImage: `url(${guilinHero})` }"
      aria-label="桂林山水旅行风景"
    >
      <div class="auth-visual-content">
        <header class="auth-place-index">
          <span>GUILIN · 25.27° N</span>
          <small>广西壮族自治区</small>
        </header>

        <aside class="auth-agent-rail" aria-label="TravelMind 行程规划示意">
          <header>
            <span><i />AGENT TRACE</span>
            <small>桂林 · 2 DAY</small>
          </header>
          <ol>
            <li>
              <b>01</b>
              <div><strong>核对地点</strong><small>3 个真实地点</small></div>
            </li>
            <li>
              <b>02</b>
              <div><strong>连接路线</strong><small>沿漓江向南</small></div>
            </li>
            <li>
              <b>03</b>
              <div><strong>平衡节奏</strong><small>步行与停留时间</small></div>
            </li>
          </ol>
        </aside>

        <article class="auth-route-note" aria-label="桂林旅行路线示意">
          <div class="auth-route-heading">
            <span>沿漓江向南</span>
            <small>03 个停靠点</small>
          </div>
          <div class="auth-route-line" aria-hidden="true">
            <i />
            <i />
            <i />
          </div>
          <ol>
            <li><b>01</b><span>象鼻山</span></li>
            <li><b>02</b><span>漓江</span></li>
            <li><b>03</b><span>阳朔</span></li>
          </ol>
        </article>
      </div>
    </section>

    <section class="auth-panel">
      <form class="auth-form" @submit.prevent="submit">
        <div class="auth-logo" aria-label="TravelMind">
          <svg viewBox="0 0 42 34" aria-hidden="true">
            <path d="M4 28 15 10l8 13 5-8 10 13H4Z" />
            <circle cx="28" cy="7" r="4" />
          </svg>
          <strong>TravelMind</strong>
        </div>
        <h2>{{ isRegister ? "创建账号" : "欢迎回来" }}</h2>

        <label v-if="isRegister" class="stacked-field">
          <span>用户名</span>
          <input v-model.trim="username" minlength="3" maxlength="50" required />
        </label>
        <label class="stacked-field">
          <span>邮箱</span>
          <input v-model.trim="email" type="email" autocomplete="email" required />
        </label>
        <label class="stacked-field">
          <span>密码</span>
          <input v-model="password" type="password" autocomplete="current-password" minlength="8" required />
        </label>

        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <button class="primary-action auth-submit" :disabled="loading">
          {{ loading ? "请稍候…" : isRegister ? "创建并登录" : "登录" }}
        </button>
        <button
          class="text-button"
          type="button"
          @click="mode = isRegister ? 'login' : 'register'; error = ''"
        >
          {{ isRegister ? "已有账号？返回登录" : "没有账号？现在注册" }}
        </button>
      </form>
    </section>
  </main>
</template>
