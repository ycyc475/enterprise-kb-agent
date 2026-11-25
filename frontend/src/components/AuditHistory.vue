<script setup lang="ts">
import { ref, computed, onMounted, watch } from "vue";
import { ElMessage } from "element-plus";
import { get, errorMessage } from "../api";
interface Event {
  id: number;
  action: string;
  action_label: string;
  entity_type: string;
  entity_id: string;
  actor_label: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  created_at: string;
}
const props = defineProps<{ entityType?: string; entityId?: string }>();
const items = ref<Event[]>([]),
  next = ref<number | null>(null),
  loading = ref(false),
  type = ref(""),
  action = ref(""),
  actions = ref<Record<string, string>>({}),
  selected = ref<Event | null>(null);
const labels: Record<string, string> = {
  rule: "费用规则",
  document: "文档",
  account: "账号",
  profile: "员工资料",
  knowledge_base: "知识库/授权",
};
const fields: Record<string, string> = {
  name: "名称",
  title: "文档名称",
  kind: "费用类型",
  grade: "职级",
  cities: "城市名单",
  unit_rate: "单价（元）",
  source_quote: "依据摘录",
  status: "状态",
  revision: "修订号",
  confirmed_at: "核对时间",
  suspension_reason: "暂停原因",
  version_no: "文档版本",
  format: "格式",
  effective_from: "生效时间",
  effective_until: "失效时间",
  attempt_no: "处理次数",
  error_code: "原因代码",
  username: "账号",
  role: "角色",
  is_active: "是否启用",
  auth_version: "登录版本",
  must_change_password: "是否要求改密",
  display_name: "姓名",
  department: "部门",
  description: "说明",
  member_names: "授权员工账号",
  sha256: "原文校验值",
};
const values: Record<string, string> = {
  active: "已启用",
  draft: "草稿",
  suspended: "已暂停",
  queued: "待处理",
  processing: "处理中",
  ready: "已发布",
  failed: "处理失败",
  deleted: "已删除",
  lodging: "住宿",
  daily: "补贴",
  admin: "管理员",
  employee: "员工",
  SOURCE_CHANGED: "来源版本变化",
  DOCUMENT_DELETED: "来源文档删除",
  SOURCE_EXPIRED: "来源已到期",
  KNOWLEDGE_BASE_DISABLED: "知识库已停用",
  INDEX_CONFIG_CHANGED: "索引配置变化",
  SOURCE_NOT_EFFECTIVE: "来源尚未生效",
  SOURCE_QUOTE_CHANGED: "摘录已变化",
  ADMIN_SUSPENDED: "管理员暂停",
};
const detailRows = computed(() =>
  selected.value
    ? [
        ...new Set([
          ...Object.keys(selected.value.before || {}),
          ...Object.keys(selected.value.after || {}),
        ]),
      ]
        .filter((k) => k in fields)
        .map((key) => ({
          key,
          label: fields[key],
          before: selected.value?.before?.[key],
          after: selected.value?.after?.[key],
        }))
    : [],
);
function display(value: unknown, key: string) {
  if (value == null) return "—";
  if (
    ["confirmed_at", "effective_from", "effective_until"].includes(key) &&
    typeof value === "string"
  )
    return time(value);
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.length ? value.join("、") : "无";
  return values[String(value)] || String(value);
}
function time(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    dateStyle: "short",
    timeStyle: "medium",
    hour12: false,
  }).format(new Date(value));
}
function target(event: Event) {
  const data = event.after || event.before;
  return String(
    data?.name ||
      data?.title ||
      data?.username ||
      data?.display_name ||
      labels[event.entity_type],
  );
}
let generation = 0;
async function load(more = false) {
  const token = ++generation;
  loading.value = true;
  const query = new URLSearchParams({ limit: "30" });
  if (props.entityType || type.value)
    query.set("entity_type", props.entityType || type.value);
  if (props.entityId) query.set("entity_id", props.entityId);
  if (action.value) query.set("action", action.value);
  if (more && next.value) query.set("before_id", String(next.value));
  try {
    const result = await get<{
      items: Event[];
      next_before_id: number | null;
      actions: Record<string, string>;
    }>("/admin/audit-events?" + query);
    if (token !== generation) return;
    items.value = more ? [...items.value, ...result.items] : result.items;
    next.value = result.next_before_id;
    actions.value = result.actions;
  } catch (e) {
    ElMessage.error(errorMessage(e));
  } finally {
    if (token === generation) loading.value = false;
  }
}
watch([() => props.entityId, type, action], () => void load());
onMounted(() => void load());
</script>
<template>
  <div class="audit-toolbar">
    <el-select
      v-if="!entityType"
      v-model="type"
      aria-label="操作对象筛选"
      placeholder="全部管理对象"
      clearable
      ><el-option
        v-for="(name, key) in labels"
        :key="key"
        :label="name"
        :value="key" /></el-select
    ><el-select
      v-model="action"
      aria-label="操作类型筛选"
      placeholder="全部操作类型"
      clearable
      ><el-option
        v-for="(name, key) in actions"
        :key="key"
        :label="name"
        :value="key" /></el-select
    ><el-button :loading="loading" @click="load()">刷新记录</el-button>
  </div>
  <el-table
    :data="items"
    v-loading="loading"
    empty-text="暂无此范围内的操作记录"
    class="audit-table"
    ><el-table-column label="时间（上海）" min-width="155"
      ><template #default="{ row }">{{
        time(row.created_at)
      }}</template></el-table-column
    ><el-table-column
      prop="actor_label"
      label="操作者"
      min-width="100"
    /><el-table-column
      prop="action_label"
      label="操作"
      min-width="180"
    /><el-table-column label="对象" min-width="180"
      ><template #default="{ row }"
        >{{ target(row)
        }}<small class="subtle audit-object">{{
          labels[row.entity_type]
        }}</small></template
      ></el-table-column
    ><el-table-column label="详情" width="85"
      ><template #default="{ row }"
        ><el-button link @click="selected = row">查看变更</el-button></template
      ></el-table-column
    ></el-table
  >
  <el-button
    v-if="next"
    class="space-top"
    :loading="loading"
    @click="load(true)"
    >加载更早记录</el-button
  >
  <el-drawer
    :model-value="!!selected"
    title="管理变更详情"
    size="min(780px,96vw)"
    @close="selected = null"
    ><template v-if="selected"
      ><h3>{{ selected.action_label }}</h3>
      <p class="subtle">
        {{ selected.actor_label }} · {{ time(selected.created_at) }}
      </p>
      <el-alert
        v-if="selected.action === 'rule.baseline'"
        title="这是迁移时的现状快照，不代表此前的完整操作历史。"
        type="info"
        :closable="false"
      /><el-table :data="detailRows"
        ><el-table-column
          prop="label"
          label="字段"
          width="115"
        /><el-table-column label="变更前" min-width="190"
          ><template #default="{ row }">
            <pre class="audit-value">{{ display(row.before, row.key) }}</pre>
          </template></el-table-column
        ><el-table-column label="变更后" min-width="190"
          ><template #default="{ row }">
            <pre class="audit-value">{{ display(row.after, row.key) }}</pre>
          </template></el-table-column
        ></el-table
      ></template
    ></el-drawer
  >
</template>
