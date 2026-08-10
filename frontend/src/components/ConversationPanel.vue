<script setup>
import { computed, nextTick, ref, watch } from "vue";

const props = defineProps({
  conversation: { type: Object, default: null },
  busy: { type: Boolean, default: false },
  error: { type: String, default: "" },
});
const emit = defineEmits([
  "send",
  "apply",
  "dismiss",
  "apply-draft",
  "dismiss-draft",
]);

const draftText = ref("");
const scrollArea = ref(null);

const isGenerated = computed(() => props.conversation?.status === "generated");
const messages = computed(() => props.conversation?.messages || []);
const hasPendingProposal = computed(() => Boolean(
  props.conversation?.pending_proposal_id,
));
const hasPendingDraftPreview = computed(() => messages.value.some((message) => (
  message.payload?.kind === "draft_preview" && message.payload?.status === "pending"
)));
function send() {
  const content = draftText.value.trim();
  if (!content || props.busy) return;
  emit("send", content);
  draftText.value = "";
}

function clock(value) {
  if (!value) return "";
  return new Date(value).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function proposalRows(message) {
  return message.payload?.preview || [];
}

function draftPreviewRows(message) {
  return message.payload?.preview || [];
}

function proposalLabel(row) {
  if (typeof row === "string") return row;
  const action = { add_activity: "新增", update_activity: "调整", remove_activity: "移除", move_activity: "移动" }[row.type] || "优化";
  const name = row.activity_name || row.after?.name || row.before?.name || "行程安排";
  return `${action} ${name}`;
}

function proposalId(message) {
  return message.modification_proposal_id || message.payload?.proposal_id;
}

watch(
  () => messages.value.length,
  async () => {
    await nextTick();
    if (scrollArea.value) scrollArea.value.scrollTop = scrollArea.value.scrollHeight;
  },
  { immediate: true },
);
</script>

<template>
  <section class="conversation-panel" aria-label="与 TravelMind 对话">
    <div ref="scrollArea" class="message-scroll">
      <div v-if="!conversation" class="conversation-loading">正在打开对话…</div>

      <article
        v-for="message in messages"
        :key="message.id"
        class="message-block"
        :class="[`role-${message.role}`, `type-${message.message_type}`]"
      >
        <time>{{ clock(message.created_at) }}</time>
        <div v-if="message.role === 'assistant'" class="agent-mark" aria-hidden="true">
          <svg viewBox="0 0 28 28"><path d="M3 23 11 8l5 10 4-7 6 12H3Z" /></svg>
        </div>
        <div class="message-content">
          <p>{{ message.content }}</p>

          <!-- === 模块：AI 需求预览条 === -->
          <!-- 流程：候选草稿 → 展示完整字段 → 用户确认/放弃 → 后端原子合并 -->
          <section
            v-if="message.payload?.kind === 'draft_preview'"
            class="draft-preview-strip"
            :class="`is-${message.payload?.status || 'pending'}`"
          >
            <header>
              <strong>需求预览</strong>
              <span>{{ message.payload?.status === 'pending' ? '待确认' : message.payload?.status === 'applied' ? '已应用' : message.payload?.status === 'stale' ? '已过期' : '已放弃' }}</span>
            </header>
            <dl>
              <div v-for="row in draftPreviewRows(message)" :key="row.field">
                <dt>{{ row.label }}</dt>
                <dd>{{ row.value || '待补充' }}<small v-if="row.assumed">AI 默认</small></dd>
              </div>
            </dl>
            <div v-if="message.payload?.status === 'pending'" class="draft-preview-actions">
              <button type="button" :disabled="busy" @click="emit('dismiss-draft', message.id)">放弃</button>
              <button type="button" :disabled="busy" @click="emit('apply-draft', message.id)">确认并生成</button>
            </div>
          </section>

          <section v-if="message.message_type === 'proposal'" class="proposal-preview">
            <header>
              <span class="spark">✦</span>
              <strong>行程优化建议</strong>
            </header>
            <p>我会先展示变化，只有你确认后才会改动原行程。</p>
            <ul>
              <li v-for="(row, index) in proposalRows(message)" :key="index">
                <span>{{ index + 1 }}</span>
                <strong>{{ proposalLabel(row) }}</strong>
              </li>
            </ul>
            <div v-if="message.payload?.status === 'pending'" class="proposal-buttons">
              <button type="button" :disabled="busy" @click="emit('dismiss', proposalId(message))">暂不修改</button>
              <button type="button" :disabled="busy" @click="emit('apply', proposalId(message))">应用新行程</button>
            </div>
            <p v-else class="proposal-resolved">{{ message.payload?.status === 'applied' ? '这份调整已经应用。' : message.payload?.status === 'stale' ? '你提出了新的修改，这份旧提案已过期。' : '这份调整已取消。' }}</p>
          </section>
        </div>
      </article>

      <div v-if="busy" class="agent-thinking"><i /><i /><i /><span>TravelMind 正在思考</span></div>
      <p v-if="error" class="chat-error" role="alert">{{ error }}</p>
    </div>

    <form class="chat-composer" @submit.prevent="send">
      <textarea
        v-model="draftText"
        :placeholder="hasPendingProposal ? '继续补充修改，新方案会替换当前建议…' : isGenerated ? '告诉我还想怎样调整…' : '继续告诉我们你的想法…'"
        :disabled="busy"
        rows="1"
        maxlength="1000"
        @keydown.enter.exact.prevent="send"
      />
      <button type="submit" :disabled="busy || !draftText.trim()" aria-label="发送消息">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 5 16 7-16 7 3-7-3-7Z" /><path d="M7 12h13" /></svg>
      </button>
    </form>
  </section>
</template>
