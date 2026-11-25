<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, get, post, patch, errorMessage } from "../api";
import { useAuth } from "../stores/auth";
import type { KnowledgeBase, Page } from "../types";
import ExpenseForm from "../components/ExpenseForm.vue";
interface Expense {
  kind: string;
  quantity?: number | null;
  actual_amount?: string | null;
}
interface Trip {
  destination?: string | null;
  travel_days?: number | null;
  lodging_nights?: number | null;
}
interface Conversation {
  id: string;
  title: string;
  knowledge_base_ids: string[];
  trip_context: Trip;
  expense_context?: Expense;
  context_revision: number;
  scope_changed: boolean;
  active_run_id: string | null;
  latest_run: Run | null;
}
interface Citation {
  id: string;
  index: number;
  title: string;
  version_no: number;
  anchor: Record<string, unknown>;
}
interface Message {
  id: string;
  role: string;
  status: string;
  content: string | null;
  citations: Citation[];
  notice?: string;
  error?: { code: string; message: string; stage: string; detail?: string };
  expense_proposal?: {
    expense_context: Expense;
    context_revision: number;
    profile_revision: number;
  };
  trip_proposal?: {
    trip_context: Trip;
    context_revision: number;
    profile_revision: number;
  };
  calculation?: {
    id: string;
    status: string;
    total_amount: string | null;
    currency: string;
    profile: { grade: string };
    trip_context: Trip;
    expense_context?: Expense | null;
    items: {
      kind: string;
      label: string;
      quantity: number;
      unit: string;
      unit_rate: string;
      amount: string;
      formula?: string | null;
      source?: { title: string; version_no: number };
    }[];
    missing_fields: string[];
    blocked_reasons: { message: string }[];
  };
}
interface Run {
  id: string;
  status: string;
  search_attempts: number;
  model_attempts: number;
  stop_reason: string | null;
  limits: { search: number; model: number; seconds: number };
}
interface Source {
  title: string;
  original_name: string;
  version_no: number;
  content: string;
  quote: string;
  anchor: Record<string, unknown>;
  download_path: string;
}
const auth = useAuth(),
  route = useRoute(),
  router = useRouter();
const kbs = ref<KnowledgeBase[]>([]),
  selected = ref<string[]>([]),
  loading = ref(true),
  ready = ref(false),
  model = ref("");
const limits = ref({ search: 5, model: 10, seconds: 180 });
const expenseLabels: Record<string, string> = {
  office: "办公采购",
  training: "培训报销",
  hospitality: "业务招待",
};
const conversations = ref<Conversation[]>([]),
  nextOffset = ref<number | null>(null),
  current = ref<Conversation | null>(null);
const values = ref<Message[]>([]),
  before = ref<string | null>(null),
  run = ref<Run | null>(null),
  progress = ref<string[]>([]);
const question = ref(""),
  sending = ref(false),
  error = ref(""),
  polling = ref(false),
  destination = ref(""),
  saving = ref(false);
const days = ref<number | undefined>(),
  nights = ref<number | undefined>();
const pending = ref<{
  conversationId: string;
  body: Record<string, unknown>;
} | null>(null);
const drawer = ref(false),
  source = ref<Source | null>(null),
  sourceLoading = ref(false),
  bottom = ref<HTMLElement | null>(null);
const active = computed(
  () => !!run.value && ["queued", "running"].includes(run.value.status),
);
const busy = computed(
  () => loading.value || active.value || sending.value || saving.value,
);
const trip = computed<Trip>(() => ({
  destination: destination.value.trim() || null,
  travel_days: days.value ?? null,
  lodging_nights: nights.value ?? null,
}));
const tripDirty = computed(() =>
  ["destination", "travel_days", "lodging_nights"].some(
    (key) =>
      (trip.value[key as keyof Trip] ?? null) !==
      (current.value?.trip_context[key as keyof Trip] ?? null),
  ),
);
const scopeDirty = computed(
  () =>
    JSON.stringify([...selected.value].sort()) !==
    JSON.stringify([...(current.value?.knowledge_base_ids || [])].sort()),
);
const statuses: Record<string, string> = {
  queued: "等待开始",
  running: "正在查找依据并组织回答",
  completed: "回答已完成",
  needs_clarification: "需要补充条件",
  insufficient_evidence: "当前依据不足",
  partial: "已返回可核对部分",
  failed: "本轮未完成",
  cancelled: "已取消",
  timeout: "已达到本轮时间上限",
  source_unavailable: "来源不可用",
};
const reasons: Record<string, string> = {
  SEARCH_LIMIT: "本轮检索额度已用完",
  MODEL_LIMIT: "本轮模型请求额度已用完",
  DEADLINE: "已达到本轮时间上限",
  SCOPE_CHANGED: "知识库授权发生变化",
  SOURCE_UNAVAILABLE: "文档依据已更新或不可访问",
  AUTH_CHANGED: "登录状态发生变化",
  CONTEXT_VERSION_CONFLICT: "员工资料或会话条件已更新",
  INVALID_CITATION: "引用未通过原文校验",
  MISSING_CITATION: "回答缺少可核验引用",
  EVIDENCE_REVIEW_FAILED: "证据相关性核验未完成",
  INVALID_MODEL_OUTPUT: "回答结构校验未通过",
  PROVIDER_UNAVAILABLE: "模型服务暂不可用",
  BUDGET_PAUSED: "应用预算已暂停新增调用",
  INTERRUPTED: "服务重启中断了本轮，请重新提问",
  CANCELLED: "用户已取消本轮",
};
let timer: ReturnType<typeof setInterval> | undefined,
  eventSeq = 0,
  failures = 0,
  alive = true;
function stopPoll() {
  if (timer) clearInterval(timer);
  timer = undefined;
}
function setTrip(value: Trip) {
  destination.value = value.destination || "";
  days.value = value.travel_days ?? undefined;
  nights.value = value.lodging_nights ?? undefined;
}
async function scrollBottom() {
  await nextTick();
  bottom.value?.scrollIntoView({ block: "nearest" });
}
async function listConversations(more = false) {
  const data = await get<{ items: Conversation[]; next_offset: number | null }>(
    `/conversations?offset=${more ? nextOffset.value || 0 : 0}`,
  );
  conversations.value = more
    ? [...conversations.value, ...data.items]
    : data.items;
  nextOffset.value = data.next_offset;
}
async function loadMessages(older = false) {
  if (!current.value) return;
  const id = current.value.id,
    data = await get<{ items: Message[]; next_before: string | null }>(
      `/conversations/${id}/messages${older && before.value ? "?before=" + encodeURIComponent(before.value) : ""}`,
    );
  if (current.value?.id !== id || !alive) return;
  values.value = older ? [...data.items, ...values.value] : data.items;
  before.value = data.next_before;
  if (!older) await scrollBottom();
}
async function poll() {
  if (!run.value || polling.value) return;
  const id = run.value.id;
  polling.value = true;
  try {
    const [latest, events] = await Promise.all([
      get<Run>(`/runs/${id}`),
      get<{ items: { seq: number; label: string }[]; next_seq: number }>(
        `/runs/${id}/events?after_seq=${eventSeq}`,
      ),
    ]);
    if (run.value?.id !== id || !alive) return;
    run.value = latest;
    eventSeq = events.next_seq;
    progress.value.push(...events.items.map((e) => e.label));
    failures = 0;
    if (!["queued", "running"].includes(latest.status)) {
      stopPoll();
      await loadMessages();
      await listConversations();
      await auth.loadMe();
      if (current.value) {
        current.value = await get<Conversation>(
          `/conversations/${current.value.id}`,
        );
        setTrip(current.value.trip_context);
      }
    }
  } catch (e) {
    failures++;
    error.value = errorMessage(e);
    if (failures >= 3) stopPoll();
  } finally {
    polling.value = false;
  }
}
async function open(id: string) {
  stopPoll();
  error.value = "";
  drawer.value = false;
  source.value = null;
  pending.value = null;
  pendingCalculation.value = null;
  run.value = null;
  values.value = [];
  progress.value = [];
  eventSeq = 0;
  try {
    const conv = await get<Conversation>(`/conversations/${id}`);
    if (!alive || String(route.params.id || "") !== id) return;
    current.value = conv;
    selected.value = conv.knowledge_base_ids.filter((i) =>
      kbs.value.some((k) => k.id === i),
    );
    setTrip(conv.trip_context);
    await loadMessages();
    if (conv.active_run_id) {
      run.value = await get<Run>(`/runs/${conv.active_run_id}`);
      timer = setInterval(() => void poll(), 700);
      await poll();
    } else {
      run.value = conv.latest_run;
    }
  } catch (e) {
    error.value = errorMessage(e);
  }
}
async function newChat() {
  stopPoll();
  current.value = null;
  run.value = null;
  values.value = [];
  before.value = null;
  pending.value = null;
  pendingCalculation.value = null;
  question.value = "";
  progress.value = [];
  error.value = "";
  setTrip({});
  selected.value = kbs.value.map((k) => k.id);
  await router.push("/");
}
async function refresh() {
  loading.value = true;
  try {
    const [bases, config] = await Promise.all([
      get<Page<KnowledgeBase>>("/knowledge-bases"),
      get<{
        ready: boolean;
        model: string;
        limits: { search: number; model: number; seconds: number };
      }>("/chat/config"),
    ]);
    kbs.value = bases.items;
    ready.value = config.ready;
    model.value = config.model;
    limits.value = config.limits;
    await auth.loadMe();
    await listConversations();
    if (route.params.id) await open(String(route.params.id));
    else selected.value = kbs.value.map((k) => k.id);
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    loading.value = false;
  }
}
function validTrip() {
  if (
    days.value !== undefined &&
    nights.value !== undefined &&
    nights.value >= days.value
  ) {
    ElMessage.warning("住宿晚数必须少于出差天数");
    return false;
  }
  return true;
}
async function saveScope() {
  if (!current.value || !selected.value.length) return;
  saving.value = true;
  try {
    current.value = await patch<Conversation>(
      `/conversations/${current.value.id}`,
      {
        knowledge_base_ids: selected.value,
        expected_revision: current.value.context_revision,
      },
    );
    await loadMessages();
    ElMessage.success("会话范围已更新");
  } catch (e) {
    ElMessage.error(errorMessage(e));
  } finally {
    saving.value = false;
  }
}
async function saveTrip() {
  if (!current.value || !validTrip()) return;
  saving.value = true;
  try {
    current.value = await patch<Conversation>(
      `/conversations/${current.value.id}/trip-context`,
      {
        trip_context: trip.value,
        expected_revision: current.value.context_revision,
      },
    );
    ElMessage.success("本次条件已保存");
  } catch (e) {
    ElMessage.error(errorMessage(e));
  } finally {
    saving.value = false;
  }
}
function proposalCurrent(message: Message) {
  return (
    !!current.value &&
    !!message.trip_proposal &&
    message.trip_proposal.context_revision === current.value.context_revision &&
    message.trip_proposal.profile_revision === auth.user?.profile?.revision
  );
}
async function confirmProposal(message: Message) {
  if (
    !current.value ||
    !message.trip_proposal ||
    !proposalCurrent(message) ||
    busy.value
  )
    return;
  const proposed = message.trip_proposal.trip_context;
  if (
    proposed.destination == null ||
    proposed.travel_days == null ||
    proposed.lodging_nights == null
  ) {
    setTrip(proposed);
    ElMessage.info("请在条件卡中补齐目的地、天数和晚数，再确认计算");
    return;
  }
  saving.value = true;
  try {
    current.value = await patch<Conversation>(
      `/conversations/${current.value.id}/trip-context`,
      {
        trip_context: proposed,
        expected_revision: message.trip_proposal.context_revision,
      },
    );
    setTrip(current.value.trip_context);
    pending.value = {
      conversationId: current.value.id,
      body: {
        content: "按已确认条件计算本次差旅标准预算，请使用计算工具。",
        client_request_id: crypto.randomUUID(),
        context_revision: current.value.context_revision,
        budget_confirmed_revision: current.value.context_revision,
        budget_profile_revision: message.trip_proposal.profile_revision,
      },
    };
    await deliver();
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    saving.value = false;
  }
}
function reviseProposal(message: Message) {
  if (!message.trip_proposal) return;
  setTrip(message.trip_proposal.trip_context);
  ElMessage.info("已填入条件卡，修改后点击确认并计算预算");
}
const pendingCalculation = ref<{
  conversationId: string;
  body: Record<string, unknown>;
} | null>(null);
const pendingExpense = ref<{
  conversationId: string;
  body: Record<string, unknown>;
  value: Expense;
} | null>(null);
function expenseProposalCurrent(message: Message) {
  return (
    !!current.value &&
    message.expense_proposal?.context_revision ===
      current.value.context_revision &&
    message.expense_proposal?.profile_revision === auth.user?.profile?.revision
  );
}
async function calculateExpense(
  value: Expense,
  proposal?: Message["expense_proposal"],
) {
  if (busy.value || !selected.value.length) return;
  saving.value = true;
  error.value = "";
  try {
    if (!pendingExpense.value) {
      if (!current.value)
        current.value = await post<Conversation>("/conversations", {
          knowledge_base_ids: selected.value,
        });
      if (scopeDirty.value || current.value.scope_changed) {
        ElMessage.warning("请先保存知识库范围");
        return;
      }
      pendingExpense.value = {
        conversationId: current.value.id,
        value,
        body: {
          client_request_id: crypto.randomUUID(),
          context_revision:
            proposal?.context_revision ?? current.value.context_revision,
          profile_revision:
            proposal?.profile_revision ?? auth.user?.profile?.revision,
          expense_context: value,
        },
      };
    }
    const result = await post<{ conversation: Conversation }>(
      `/conversations/${pendingExpense.value.conversationId}/expense-calculations`,
      pendingExpense.value.body,
    );
    pendingExpense.value = null;
    current.value = result.conversation;
    await router.replace("/chat/" + current.value.id);
    await loadMessages();
    await listConversations();
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    saving.value = false;
  }
}
async function calculateCard() {
  if (busy.value || !validTrip() || !selected.value.length) return;
  saving.value = true;
  error.value = "";
  try {
    if (!pendingCalculation.value) {
      if (!current.value) {
        current.value = await post<Conversation>("/conversations", {
          knowledge_base_ids: selected.value,
        });
      } else if (scopeDirty.value || current.value.scope_changed) {
        ElMessage.warning("请先保存知识库范围");
        return;
      }
      pendingCalculation.value = {
        conversationId: current.value.id,
        body: {
          client_request_id: crypto.randomUUID(),
          context_revision: current.value.context_revision,
          profile_revision: auth.user?.profile?.revision,
          trip_context: trip.value,
        },
      };
    }
    const result = await post<{ conversation: Conversation }>(
      `/conversations/${pendingCalculation.value.conversationId}/calculations`,
      pendingCalculation.value.body,
    );
    pendingCalculation.value = null;
    current.value = result.conversation;
    setTrip(current.value.trip_context);
    await router.replace("/chat/" + current.value.id);
    await loadMessages();
    await listConversations();
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    saving.value = false;
  }
}
async function deliver() {
  if (!pending.value) return;
  sending.value = true;
  error.value = "";
  try {
    run.value = await post<Run>(
      `/conversations/${pending.value.conversationId}/messages`,
      pending.value.body,
    );
    pending.value = null;
    if (current.value) await router.replace("/chat/" + current.value.id);
    question.value = "";
    progress.value = [];
    eventSeq = 0;
    failures = 0;
    if (current.value)
      current.value = await get<Conversation>(
        `/conversations/${current.value.id}`,
      );
    await loadMessages();
    stopPoll();
    timer = setInterval(() => void poll(), 700);
    await poll();
    await listConversations();
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    sending.value = false;
  }
}
async function send() {
  if (
    !question.value.trim() ||
    busy.value ||
    !ready.value ||
    !selected.value.length ||
    !validTrip()
  )
    return;
  if (pending.value) {
    await deliver();
    return;
  }
  sending.value = true;
  error.value = "";
  try {
    const submittedTrip = trip.value,
      changed = tripDirty.value;
    if (!current.value) {
      current.value = await post<Conversation>("/conversations", {
        knowledge_base_ids: selected.value,
      });
    } else if (scopeDirty.value || current.value.scope_changed) {
      ElMessage.warning("请先保存当前知识库范围");
      return;
    }
    const body: Record<string, unknown> = {
      content: question.value.trim(),
      client_request_id: crypto.randomUUID(),
      context_revision: current.value.context_revision,
    };
    if (changed) body.trip_context = submittedTrip;
    pending.value = { conversationId: current.value.id, body };
  } catch (e) {
    error.value = errorMessage(e);
    return;
  } finally {
    sending.value = false;
  }
  await deliver();
}
async function cancel() {
  if (run.value)
    try {
      await post(`/runs/${run.value.id}/cancel`);
      await poll();
    } catch (e) {
      error.value = errorMessage(e);
    }
}
async function rename() {
  if (!current.value) return;
  let value = "";
  try {
    value = (
      await ElMessageBox.prompt("输入新的会话标题", "重命名", {
        inputValue: current.value.title,
        inputPattern: /^.{1,80}$/,
        inputErrorMessage: "标题应为1至80个字符",
      })
    ).value;
  } catch {
    return;
  }
  try {
    current.value = await patch<Conversation>(
      `/conversations/${current.value.id}`,
      { title: value, expected_revision: current.value.context_revision },
    );
    await listConversations();
  } catch (e) {
    ElMessage.error(errorMessage(e));
  }
}
async function remove() {
  if (!current.value) return;
  try {
    await ElMessageBox.confirm(
      "删除后不再展示此会话，并停止未完成的问答任务。",
      "删除会话",
      { type: "warning", confirmButtonText: "删除会话" },
    );
  } catch {
    return;
  }
  try {
    await api.delete(`/conversations/${current.value.id}`);
    await newChat();
    await listConversations();
  } catch (e) {
    ElMessage.error(errorMessage(e));
  }
}
async function viewCitation(cite: Citation) {
  source.value = null;
  drawer.value = true;
  sourceLoading.value = true;
  try {
    source.value = await get<Source>(`/citations/${cite.id}`);
  } catch (e) {
    drawer.value = false;
    error.value = errorMessage(e);
    await loadMessages();
  } finally {
    sourceLoading.value = false;
  }
}
async function download() {
  if (!source.value) return;
  try {
    const response = await api.get(source.value.download_path, {
        responseType: "blob",
      }),
      url = URL.createObjectURL(response.data),
      a = document.createElement("a");
    a.href = url;
    a.download = source.value.original_name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    error.value = errorMessage(e);
  }
}
function location(a: Record<string, unknown>) {
  return [
    a.page ? `第${a.page}页` : "",
    a.heading || "",
    a.line_start ? `第${a.line_start}—${a.line_end}行` : "",
    a.paragraph ? `第${a.paragraph}段` : "",
    a.table ? `表${a.table} / 第${a.row}行` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}
function focus() {
  if (current.value && !active.value) void loadMessages().catch(() => {});
}
watch(
  () => route.params.id,
  (id) => {
    if (loading.value) return;
    if (id && id !== current.value?.id) void open(String(id));
    else if (!id && current.value) void newChat();
  },
);
onMounted(() => {
  void refresh();
  window.addEventListener("focus", focus);
});
onUnmounted(() => {
  alive = false;
  stopPoll();
  window.removeEventListener("focus", focus);
});
</script>
<template>
  <div class="page-heading">
    <div>
      <span class="eyebrow muted">YOUR KNOWLEDGE WORKSPACE</span>
      <h1>知识问答</h1>
      <p>基于授权文档提问，沿着引用核对每一个答案。</p>
    </div>
    <el-button :loading="loading" @click="refresh">刷新可访问知识库</el-button>
  </div>
  <el-alert
    v-if="!loading && !ready"
    class="space-bottom"
    title="对话服务尚未配置完成，请联系管理员检查本机模型配置。"
    type="warning"
    :closable="false"
    show-icon
  /><el-alert
    v-if="error"
    class="space-bottom"
    :title="error"
    type="error"
    :closable="false"
    show-icon
  />
  <div class="chat-workspace">
    <aside class="panel history-panel">
      <el-button type="primary" plain class="new-chat" @click="newChat"
        >＋ 新建会话</el-button
      >
      <div class="nav-caption">我的会话</div>
      <div v-if="!conversations.length" class="subtle history-empty">
        你的提问会保存在这里，仅本人可见。
      </div>
      <button
        v-for="conv in conversations"
        :key="conv.id"
        class="history-item"
        :class="{ selected: current?.id === conv.id }"
        @click="router.push('/chat/' + conv.id)"
      >
        <span>◈</span>{{ conv.title }}</button
      ><el-button
        v-if="nextOffset !== null"
        text
        @click="listConversations(true)"
        >加载更多会话</el-button
      >
    </aside>
    <section class="panel chat-panel live-chat">
      <div class="panel-toolbar">
        <span class="section-label">知识库范围</span
        ><el-select
          v-model="selected"
          multiple
          collapse-tags
          aria-label="知识库范围"
          placeholder="请选择授权知识库"
          style="width: 230px"
          :disabled="busy || loading || !!pending"
          ><el-option
            v-for="kb in kbs"
            :key="kb.id"
            :label="kb.name"
            :value="kb.id" /></el-select
        ><el-button
          v-if="current && (scopeDirty || current.scope_changed)"
          type="primary"
          link
          :disabled="busy || !selected.length"
          @click="saveScope"
          >保存范围</el-button
        >
        <div v-if="current" class="chat-menu">
          <el-button text size="small" :disabled="busy" @click="rename"
            >重命名</el-button
          ><el-button text size="small" type="danger" @click="remove"
            >删除</el-button
          >
        </div>
      </div>
      <el-alert
        v-if="current?.scope_changed"
        title="原会话范围包含已撤销的授权，请重新选择并保存知识库范围。"
        type="warning"
        :closable="false"
      />
      <div v-if="!values.length" class="chat-empty">
        <div class="knowledge-mark">◈</div>
        <span class="eyebrow muted">ANSWERS WITH EVIDENCE</span>
        <h2>让每一个回答，都有出处</h2>
        <p>
          选择知识库，提出你的工作问题。<br />支持追问、条件澄清与有限补查。
        </p>
        <div class="topic-cards">
          <button type="button" @click="question = '北京住宿标准是什么？'">
            <span>01 / 制度查询</span
            ><strong>北京住宿标准是什么？</strong></button
          ><button
            type="button"
            @click="question = '出差报销需要准备什么材料？'"
          >
            <span>02 / 办理指南</span><strong>报销前需要哪些材料？</strong>
          </button>
        </div>
      </div>
      <div v-else class="message-list">
        <el-button v-if="before" text @click="loadMessages(true)"
          >加载更早消息</el-button
        >
        <article
          v-for="message in values"
          :key="message.id"
          class="chat-message"
          :class="message.role"
          :data-testid="
            message.role === 'assistant' ? 'assistant-message' : 'user-message'
          "
        >
          <div class="message-author">
            <span class="avatar">{{
              message.role === "assistant" ? "◈" : "我"
            }}</span
            ><strong>{{
              message.role === "assistant" ? "知识助手" : "我"
            }}</strong
            ><span v-if="message.role === 'assistant'" class="subtle">{{
              statuses[message.status]
            }}</span>
          </div>
          <pre
            v-if="message.content && !message.calculation?.expense_context"
            >{{ message.content }}</pre>
          <p v-else class="unavailable-message">{{ message.notice }}</p>
          <el-alert
            v-if="message.error && message.content"
            :title="message.error.message"
            type="warning"
            :closable="false"
            show-icon
          />
          <section
            v-if="message.calculation?.expense_context"
            class="budget-result"
            data-testid="expense-result"
          >
            <div class="card-heading">
              <h3>
                {{
                  expenseLabels[message.calculation.expense_context.kind]
                }}参考额度
              </h3>
              <span class="mini-badge">程序核算 · CNY</span>
            </div>
            <p>
              数量：{{
                message.calculation.expense_context.quantity ?? "待补充"
              }}
              · 实际申请金额：{{
                message.calculation.expense_context.actual_amount ?? "待补充"
              }}元
            </p>
            <strong>{{
              message.calculation.total_amount !== null
                ? "¥" + message.calculation.total_amount
                : "暂不可用"
            }}</strong>
            <p v-for="item in message.calculation.items" :key="item.kind">
              {{ item.formula }}
            </p>
            <p
              v-for="reason in message.calculation.blocked_reasons"
              :key="reason.message"
            >
              {{ reason.message }}
            </p>
            <p v-if="message.calculation.missing_fields.length">
              请补充：{{ message.calculation.missing_fields.join("、") }}
            </p>
            <p class="subtle">
              依据当前有效规则核算，不代表实际审批、票据核验或付款。
            </p>
          </section>
          <section v-if="message.expense_proposal" class="budget-proposal">
            <h3>待确认的费用条件</h3>
            <ExpenseForm
              :initial="message.expense_proposal.expense_context"
              :disabled="
                busy || !!pendingExpense || !expenseProposalCurrent(message)
              "
              @calculate="calculateExpense($event, message.expense_proposal)"
            />
            <p v-if="!expenseProposalCurrent(message)" class="subtle">
              条件或员工资料已变化，请重新确认。
            </p>
          </section>
          <section
            v-if="message.trip_proposal"
            class="budget-proposal"
            data-testid="trip-proposal"
          >
            <h3>待确认的出差条件</h3>
            <p>
              目的地：{{
                message.trip_proposal.trip_context.destination || "待补充"
              }}
              ·
              {{ message.trip_proposal.trip_context.travel_days ?? "待补充" }}天
              ·
              {{
                message.trip_proposal.trip_context.lodging_nights ?? "待补充"
              }}晚
            </p>
            <p class="subtle">
              职级按管理员维护的本人资料取得；确认前不会计算金额。
            </p>
            <el-button
              type="primary"
              :disabled="busy || !proposalCurrent(message)"
              @click="confirmProposal(message)"
              >确认条件并计算</el-button
            >
            <el-button
              :disabled="busy || !proposalCurrent(message)"
              @click="reviseProposal(message)"
              >修改条件</el-button
            >
            <p v-if="!proposalCurrent(message)" class="subtle">
              条件或员工资料已变化，请重新确认当前条件卡。
            </p>
          </section>
          <section
            v-if="message.calculation && !message.calculation.expense_context"
            class="budget-result"
            data-testid="budget-result"
          >
            <div class="card-heading">
              <h3>按标准估算预算</h3>
              <span class="mini-badge">程序计算 · CNY</span>
            </div>
            <p class="subtle">
              {{ message.calculation.profile.grade }} ·
              {{
                message.calculation.trip_context.destination || "目的地待补充"
              }}
              · {{ message.calculation.trip_context.travel_days ?? "—" }}天 /
              {{ message.calculation.trip_context.lodging_nights ?? "—" }}晚
            </p>
            <div
              v-for="item in message.calculation.items"
              :key="item.kind"
              class="budget-line"
            >
              <span
                >{{ item.label
                }}<small
                  >{{ item.unit_rate }}元 × {{ item.quantity
                  }}{{ item.unit }}</small
                ><small v-if="item.source"
                  >{{ item.source.title }} · v{{
                    item.source.version_no
                  }}</small
                ></span
              ><strong>¥{{ item.amount }}</strong>
            </div>
            <div class="budget-total">
              <span>合计估算</span
              ><strong>{{
                message.calculation.total_amount !== null
                  ? "¥" + message.calculation.total_amount
                  : "暂不可用"
              }}</strong>
            </div>
            <p
              v-for="reason in message.calculation.blocked_reasons"
              :key="reason.message"
              class="subtle"
            >
              {{ reason.message }}
            </p>
            <p class="subtle">
              虚构演示制度下的预算额度，实际支出与财务审批另行核对。
            </p>
          </section>
          <div v-if="message.citations.length" class="answer-citations">
            <button
              v-for="cite in message.citations"
              :key="cite.id"
              @click="viewCitation(cite)"
            >
              [{{ cite.index }}] {{ cite.title }} · v{{ cite.version_no }}
            </button>
          </div>
        </article>
        <div ref="bottom"></div>
      </div>
      <div v-if="run" class="run-status" aria-live="polite">
        <span>{{ statuses[run.status] }}</span
        ><span
          >检索 {{ run.search_attempts }}/{{
            run.limits?.search ?? limits.search
          }}
          · 模型请求 {{ run.model_attempts }}/{{
            run.limits?.model ?? limits.model
          }}</span
        ><small v-if="progress.length">{{
          progress[progress.length - 1]
        }}</small
        ><small v-if="run.stop_reason">{{
          reasons[run.stop_reason] || "本轮已停止，可重新提问"
        }}</small
        ><el-button
          v-if="active"
          size="small"
          text
          type="danger"
          @click="cancel"
          >取消本轮</el-button
        >
      </div>
      <form class="live-composer" @submit.prevent="send">
        <el-input
          id="question-input"
          v-model="question"
          type="textarea"
          aria-label="输入问题"
          :rows="3"
          maxlength="1500"
          show-word-limit
          resize="none"
          placeholder="输入问题，或补充上一轮需要的条件……"
          :disabled="busy || !selected.length || !!pending"
          @keydown.ctrl.enter.prevent="send"
        />
        <div class="composer-actions">
          <span>{{ model }} · 每轮最多{{ limits.seconds }}秒</span
          ><el-button
            v-if="pending"
            type="warning"
            :loading="sending"
            @click="deliver"
            >重试原请求</el-button
          ><el-button
            v-else
            type="primary"
            native-type="submit"
            :disabled="!question.trim() || busy || !ready || !selected.length"
            :loading="sending"
            >发送 ↑</el-button
          >
        </div>
      </form>
      <p class="composer-caption">
        回答依据可通过引用核对；模型可能出错，重要事项请核对原文。
      </p>
    </section>
    <aside class="context-column">
      <section class="panel profile-card">
        <div class="card-heading">
          <h3>我的员工资料</h3>
          <span class="mini-badge">统一维护</span>
        </div>
        <dl>
          <div>
            <dt>姓名</dt>
            <dd>{{ auth.user?.profile?.display_name || "—" }}</dd>
          </div>
          <div>
            <dt>所属部门</dt>
            <dd>{{ auth.user?.profile?.department || "—" }}</dd>
          </div>
          <div>
            <dt>职级</dt>
            <dd>
              <el-tag size="small" effect="plain">{{
                auth.user?.profile?.grade || "—"
              }}</el-tag>
            </dd>
          </div>
        </dl>
        <router-link class="text-link" to="/account"
          >查看个人资料 →</router-link
        >
      </section>
      <section class="panel trip-card">
        <div class="card-heading">
          <h3>本次出差条件</h3>
          <span class="mini-badge">可补充</span>
        </div>
        <p class="subtle">
          可直接描述行程，由助手提取待确认条件；也可在此填写，确认后按当前有效规则计算。
        </p>
        <div class="field">
          <label for="trip-destination">目的地</label
          ><el-input
            id="trip-destination"
            v-model="destination"
            maxlength="40"
            placeholder="例如：北京"
            :disabled="busy || !!pending"
          />
        </div>
        <div class="field">
          <label>出差天数</label
          ><!-- Element Plus sets aria-disabled only on mount; refresh it when state changes. -->
          <el-input-number
            :key="'days-' + (busy || !!pending)"
            v-model="days"
            aria-label="出差天数"
            :min="1"
            :max="30"
            :precision="0"
            controls-position="right"
            :disabled="busy || !!pending"
          />
        </div>
        <div class="field">
          <label>住宿晚数</label
          ><el-input-number
            :key="'nights-' + (busy || !!pending)"
            v-model="nights"
            aria-label="住宿晚数"
            :min="0"
            :max="29"
            :precision="0"
            controls-position="right"
            :disabled="busy || !!pending"
          />
        </div>
        <el-button
          v-if="current"
          :disabled="busy || !tripDirty"
          :loading="saving"
          @click="saveTrip"
          >保存条件</el-button
        >
        <p v-if="tripDirty" class="subtle">发送问题时会一并保存当前条件。</p>
        <el-button
          type="primary"
          :disabled="busy || !!pending"
          :loading="saving"
          @click="calculateCard"
          >{{
            pendingCalculation ? "重试原计算请求" : "确认并计算预算"
          }}</el-button
        >
        <el-button
          v-if="pendingCalculation"
          text
          @click="pendingCalculation = null"
          >放弃重试</el-button
        >
      </section>
      <section class="panel trip-card">
        <div class="card-heading">
          <h3>其他费用核算</h3>
          <span class="mini-badge">需确认</span>
        </div>
        <ExpenseForm
          :disabled="busy || !!pendingExpense || !selected.length"
          @calculate="calculateExpense($event)"
        />
        <template v-if="pendingExpense"
          ><el-button
            @click="calculateExpense(pendingExpense.value)"
            :disabled="busy"
            >重试原费用计算请求</el-button
          ><el-button text @click="pendingExpense = null"
            >放弃重试</el-button
          ></template
        >
      </section>
      <section class="panel scope-card">
        <div class="card-heading">
          <h3>可访问知识库</h3>
          <span class="count-badge">{{ kbs.length }}</span>
        </div>
        <div v-if="!loading && !kbs.length" class="subtle empty-small">
          还没有授权知识库，请联系管理员。
        </div>
        <div v-for="kb in kbs" :key="kb.id" class="kb-summary">
          <span class="kb-icon">▤</span>
          <div>
            <strong>{{ kb.name }}</strong>
            <p>{{ kb.description || "暂无描述" }}</p>
          </div>
        </div>
      </section>
    </aside>
  </div>
  <el-drawer
    v-model="drawer"
    :title="source?.title || '核验引用来源'"
    size="min(620px,95vw)"
    ><div v-loading="sourceLoading" class="citation-detail">
      <template v-if="source"
        ><p class="subtle">
          v{{ source.version_no }} · {{ location(source.anchor) }}
        </p>
        <h3>回答引用</h3>
        <blockquote>{{ source.quote }}</blockquote>
        <h3>来源片段</h3>
        <pre>{{ source.content }}</pre>
        <el-button type="primary" plain @click="download"
          >下载原文件</el-button
        ></template
      >
    </div></el-drawer
  >
</template>
