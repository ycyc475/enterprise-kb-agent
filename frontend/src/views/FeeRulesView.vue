<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from "vue";
import { useRoute } from "vue-router";
import AuditHistory from "../components/AuditHistory.vue";
import { ElMessage } from "element-plus";
import { get, post, put, errorMessage } from "../api";
import type { KnowledgeBase, Page } from "../types";
interface Source {
  chunk_id: string;
  document_id: string;
  version_id: string;
  title: string;
  content: string;
  version_no: number;
  anchor: Record<string, unknown>;
}
interface Rule {
  id: string;
  name: string;
  kind: string;
  grade: string | null;
  cities: string[];
  unit_rate: string;
  application_cap: string | null;
  reimbursement_ratio: string;
  max_quantity: number;
  status: string;
  effective_status: string;
  revision: number;
  source_chunk_id: string;
  source_quote: string;
  source: Source | null;
  source_document_id: string | null;
  source_state_reason: string | null;
  suspension_reason: string | null;
}
interface Recovery {
  rule: Rule;
  previous_source: { title: string; version_no: number; quote: string };
  current_version_id: string | null;
  candidates: Source[];
}
const route = useRoute(),
  history = ref<Rule | null>(null),
  recovery = ref<Recovery | null>(null),
  recoveryBusy = ref(false),
  recoverySource = ref(""),
  recoveryRate = ref(""),
  recoveryQuote = ref(""),
  recoveryChecked = ref(false);
const candidate = computed(() =>
  recovery.value?.candidates.find((c) => c.chunk_id === recoverySource.value),
);
const stateReasons: Record<string, string> = {
  SOURCE_CHANGED: "制度版本已更新",
  DOCUMENT_DELETED: "来源文档已删除",
  SOURCE_EXPIRED: "来源已到期",
  KNOWLEDGE_BASE_DISABLED: "知识库已停用",
  INDEX_CONFIG_CHANGED: "索引配置已变化",
  SOURCE_NOT_EFFECTIVE: "来源尚未生效",
  SOURCE_QUOTE_CHANGED: "原文摘录已变化",
  ADMIN_SUSPENDED: "管理员手动暂停",
};
interface Doc {
  id: string;
  title: string;
  current_version: { id: string; effective: boolean } | null;
}
const rules = ref<Rule[]>([]),
  kbs = ref<KnowledgeBase[]>([]),
  docs = ref<Doc[]>([]),
  chunks = ref<Source[]>([]);
const loading = ref(false),
  saving = ref(false),
  dialog = ref(false),
  editing = ref<Rule | null>(null),
  reviewing = ref<Rule | null>(null),
  checked = ref(false);
const selectedKb = ref(""),
  selectedDoc = ref(""),
  sourceBusy = ref(false);
const form = reactive({
  name: "",
  kind: "lodging",
  grade: "S1",
  cities: "北京,上海",
  unit_rate: "300.00",
  application_cap: "",
  reimbursement_ratio: "1",
  max_quantity: 100,
  source_chunk_id: "",
  source_quote: "",
});
const selectedChunk = computed(() =>
  chunks.value.find((c) => c.chunk_id === form.source_chunk_id),
);
const kindLabels: Record<string, string> = {
  lodging: "住宿标准",
  daily: "每日补贴",
  office: "办公用品采购",
  training: "培训报销",
  hospitality: "业务招待",
};
const units: Record<string, string> = {
  lodging: "晚",
  daily: "天",
  office: "套",
  training: "人",
  hospitality: "人",
};
const extended = computed(() =>
  ["office", "training", "hospitality"].includes(form.kind),
);
function scopeLabel(rule: Rule) {
  if (rule.kind === "lodging")
    return `${rule.grade}：${rule.cities.join("、")}`;
  if (rule.kind === "daily") return "全体员工统一日补贴";
  return `最多${rule.max_quantity}${units[rule.kind]}/单；比例${rule.reimbursement_ratio}；单次上限${rule.application_cap}元`;
}
const statuses: Record<string, string> = {
  draft: "草稿",
  active: "已启用",
  suspended: "已暂停",
  source_unavailable: "来源已失效",
};
const filtered = computed(() =>
  route.query.document_id
    ? rules.value.filter(
        (r) => r.source_document_id === route.query.document_id,
      )
    : rules.value,
);
const pendingRules = computed(
  () => filtered.value.filter((r) => r.effective_status !== "active").length,
);
async function openRecovery(rule: Rule) {
  recoveryBusy.value = true;
  try {
    recovery.value = await get<Recovery>(
      `/admin/fee-rules/${rule.id}/source-review`,
    );
    recoverySource.value = "";
    recoveryQuote.value = "";
    recoveryRate.value = recovery.value.rule.unit_rate;
    recoveryChecked.value = false;
  } catch (e) {
    ElMessage.error(errorMessage(e));
  } finally {
    recoveryBusy.value = false;
  }
}
function selectRecoverySource() {
  recoveryQuote.value = candidate.value?.content || "";
}
watch([recoverySource, recoveryRate, recoveryQuote], () => {
  recoveryChecked.value = false;
});
async function recoverRule() {
  if (!recovery.value || !candidate.value || !recoveryChecked.value) return;
  saving.value = true;
  try {
    await post(`/admin/fee-rules/${recovery.value.rule.id}/recover`, {
      expected_revision: recovery.value.rule.revision,
      expected_source_version_id: recovery.value.current_version_id,
      source_chunk_id: recoverySource.value,
      source_quote: recoveryQuote.value,
      unit_rate: recoveryRate.value,
      source_checked: true,
    });
    recovery.value = null;
    ElMessage.success("已核对新来源并恢复规则，请重新计算预算");
    await load();
  } catch (e) {
    ElMessage.error(errorMessage(e));
  } finally {
    saving.value = false;
  }
}
async function load() {
  loading.value = true;
  try {
    [rules.value, kbs.value] = await Promise.all([
      get<{ items: Rule[] }>("/admin/fee-rules").then((r) => r.items),
      get<Page<KnowledgeBase>>("/knowledge-bases").then((r) => r.items),
    ]);
  } catch (e) {
    ElMessage.error(errorMessage(e));
  } finally {
    loading.value = false;
  }
}
async function chooseKb() {
  selectedDoc.value = "";
  chunks.value = [];
  form.source_chunk_id = "";
  form.source_quote = "";
  if (!selectedKb.value) return;
  sourceBusy.value = true;
  try {
    docs.value = (
      await get<Page<Doc>>(`/knowledge-bases/${selectedKb.value}/documents`)
    ).items.filter((d) => d.current_version?.effective);
  } catch (e) {
    ElMessage.error(errorMessage(e));
  } finally {
    sourceBusy.value = false;
  }
}
async function chooseDoc() {
  const doc = docs.value.find((d) => d.id === selectedDoc.value);
  chunks.value = [];
  form.source_chunk_id = "";
  form.source_quote = "";
  if (!doc?.current_version) return;
  sourceBusy.value = true;
  try {
    chunks.value = (
      await get<{ items: Source[] }>(
        `/admin/document-versions/${doc.current_version.id}/chunks`,
      )
    ).items;
  } catch (e) {
    ElMessage.error(errorMessage(e));
  } finally {
    sourceBusy.value = false;
  }
}
function chooseChunk() {
  form.source_quote = selectedChunk.value?.content.slice(0, 2000) || "";
}
async function edit(rule: Rule | null) {
  editing.value = rule;
  selectedKb.value = "";
  selectedDoc.value = "";
  docs.value = [];
  chunks.value = [];
  Object.assign(
    form,
    rule
      ? {
          name: rule.name,
          kind: rule.kind,
          grade: rule.grade || "S1",
          cities: rule.cities.join(","),
          unit_rate: rule.unit_rate,
          application_cap: rule.application_cap || "",
          reimbursement_ratio: rule.reimbursement_ratio,
          max_quantity: rule.max_quantity,
          source_chunk_id: rule.source_chunk_id,
          source_quote: rule.source_quote,
        }
      : {
          name: "",
          kind: "lodging",
          grade: "S1",
          cities: "北京,上海",
          unit_rate: "300.00",
          application_cap: "",
          reimbursement_ratio: "1",
          max_quantity: 100,
          source_chunk_id: "",
          source_quote: "",
        },
  );
  if (rule?.source) chunks.value = [rule.source];
  dialog.value = true;
}
async function save() {
  if (!form.name.trim() || !form.source_chunk_id || !form.source_quote.trim()) {
    ElMessage.warning("请填写名称并选择原文来源");
    return;
  }
  saving.value = true;
  const body = {
    name: form.name,
    kind: form.kind,
    grade: form.kind === "lodging" ? form.grade : null,
    cities:
      form.kind === "lodging"
        ? form.cities
            .split(/[,，、]/)
            .map((c) => c.trim())
            .filter(Boolean)
        : [],
    unit_rate: form.unit_rate,
    application_cap: extended.value ? form.application_cap : null,
    reimbursement_ratio:
      form.kind === "training" ? form.reimbursement_ratio : "1",
    max_quantity: extended.value ? form.max_quantity : 100,
    source_chunk_id: form.source_chunk_id,
    source_quote: form.source_quote,
  };
  try {
    if (editing.value)
      await put(`/admin/fee-rules/${editing.value.id}`, {
        ...body,
        expected_revision: editing.value.revision,
      });
    else await post("/admin/fee-rules", body);
    dialog.value = false;
    ElMessage.success("已保存为草稿，核对原文后方可启用");
    await load();
  } catch (e) {
    ElMessage.error(errorMessage(e));
  } finally {
    saving.value = false;
  }
}
function review(rule: Rule) {
  reviewing.value = rule;
  checked.value = false;
}
async function transition(rule: Rule, activate: boolean) {
  saving.value = true;
  try {
    await post(
      `/admin/fee-rules/${rule.id}/${activate ? "activate" : "suspend"}`,
      {
        expected_revision: rule.revision,
        ...(activate ? { source_checked: true } : {}),
      },
    );
    reviewing.value = null;
    ElMessage.success(activate ? "规则已启用" : "规则已暂停");
    await load();
  } catch (e) {
    ElMessage.error(errorMessage(e));
  } finally {
    saving.value = false;
  }
}
onMounted(load);
</script>
<template>
  <div class="page-heading">
    <div>
      <span class="eyebrow muted">POLICY STANDARD BUDGET</span>
      <h1>费用规则</h1>
      <p>核对原文中的适用条件与标准，再启用计算规则。金额由程序计算。</p>
    </div>
    <el-button type="primary" @click="edit(null)">新建规则</el-button>
  </div>
  <el-alert
    class="space-bottom"
    title="演示制度均为虚构资料。修改规则会退回草稿；来源替换或删除后，关联规则暂停。"
    type="info"
    :closable="false"
  />
  <el-alert
    v-if="pendingRules"
    class="space-bottom"
    :title="`${pendingRules}条规则需要处理：核对当前文档、单价与适用范围后恢复；其他有效规则继续可用。`"
    type="warning"
    :closable="false"
  />
  <p v-if="route.query.document_id" class="subtle">
    仅展示所选文档关联的规则。<router-link to="/admin/fee-rules"
      >查看全部规则</router-link
    >
  </p>
  <section class="panel table-panel">
    <div class="table-heading">
      <h2>
        标准与来源 <span class="inline-count">{{ rules.length }}</span>
      </h2>
      <el-button :loading="loading" @click="load">刷新</el-button>
    </div>
    <el-table
      :data="filtered"
      v-loading="loading"
      empty-text="暂无规则，请先上传制度并创建草稿"
    >
      <el-table-column label="规则" min-width="190"
        ><template #default="{ row }"
          ><strong>{{ row.name }}</strong>
          <p class="subtle">
            {{ kindLabels[row.kind] + " · " + scopeLabel(row) }}
          </p></template
        ></el-table-column
      >
      <el-table-column label="标准" min-width="130"
        ><template #default="{ row }"
          >¥{{ row.unit_rate }} / {{ units[row.kind] }}</template
        ></el-table-column
      >
      <el-table-column label="原文依据" min-width="190"
        ><template #default="{ row }"
          >{{ row.source?.title || "来源不可用，请重新关联" }}
          <p v-if="row.source" class="subtle">
            v{{ row.source.version_no }} ·
            {{ row.source.anchor.heading || "原文片段" }}
          </p></template
        ></el-table-column
      >
      <el-table-column label="状态" min-width="110"
        ><template #default="{ row }"
          ><el-tag
            :type="row.effective_status === 'active' ? 'success' : 'info'"
            >{{ statuses[row.effective_status] }}</el-tag
          >
          <p class="subtle">版本 {{ row.revision }}</p>
          <p
            v-if="row.source_state_reason || row.suspension_reason"
            class="subtle"
          >
            {{
              stateReasons[row.source_state_reason || row.suspension_reason] ||
              "请核对来源"
            }}
          </p></template
        ></el-table-column
      >
      <el-table-column label="操作" min-width="255"
        ><template #default="{ row }"
          ><el-button link @click="edit(row)">编辑</el-button
          ><el-button
            v-if="row.status === 'active'"
            link
            type="warning"
            :disabled="saving"
            @click="transition(row, false)"
            >暂停</el-button
          ><el-button
            v-else
            link
            type="primary"
            :disabled="!row.source || saving"
            @click="review(row)"
            >核对启用</el-button
          ><el-button
            v-if="row.status !== 'active'"
            link
            type="primary"
            :disabled="saving || recoveryBusy"
            @click="openRecovery(row)"
            >核对恢复</el-button
          ><el-button link @click="history = row">历史</el-button></template
        ></el-table-column
      >
    </el-table>
  </section>
  <el-dialog
    v-model="dialog"
    :title="editing ? '编辑费用规则' : '新建费用规则'"
    width="min(720px,95vw)"
    :close-on-click-modal="false"
  >
    <el-form label-position="top"
      ><el-form-item label="规则名称"
        ><el-input v-model="form.name" aria-label="规则名称" maxlength="100"
      /></el-form-item>
      <div class="rule-form-grid">
        <el-form-item label="费用类型"
          ><el-select v-model="form.kind" aria-label="费用类型"
            ><el-option label="住宿标准" value="lodging" /><el-option
              label="每日补贴"
              value="daily" /><el-option
              label="办公用品采购"
              value="office" /><el-option
              label="培训报销"
              value="training" /><el-option
              label="业务招待"
              value="hospitality" /></el-select
        ></el-form-item>
        <el-form-item
          :label="extended ? '单位上限（元）' : '单价（元，最多两位小数）'"
          ><el-input v-model="form.unit_rate" aria-label="规则单价"
        /></el-form-item>
      </div>
      <div v-if="form.kind === 'lodging'" class="rule-form-grid">
        <el-form-item label="适用职级"
          ><el-select v-model="form.grade" aria-label="适用职级"
            ><el-option label="S1" value="S1" /><el-option
              label="S2"
              value="S2" /></el-select></el-form-item
        ><el-form-item label="城市名单（逗号分隔，精确匹配）"
          ><el-input v-model="form.cities" aria-label="城市名单"
        /></el-form-item>
      </div>
      <div v-if="extended" class="rule-form-grid">
        <el-form-item label="单次金额上限（元）"
          ><el-input v-model="form.application_cap" aria-label="单次金额上限"
        /></el-form-item>
        <el-form-item label="单次数量上限"
          ><el-input-number
            v-model="form.max_quantity"
            :min="1"
            :max="100"
            :precision="0"
            aria-label="单次数量上限"
        /></el-form-item>
        <el-form-item
          v-if="form.kind === 'training'"
          label="报销比例（0～1，例如0.8）"
          ><el-input v-model="form.reimbursement_ratio" aria-label="报销比例"
        /></el-form-item>
      </div>
      <el-divider content-position="left">选择当前有效原文</el-divider>
      <div class="rule-form-grid">
        <el-form-item label="来源知识库"
          ><el-select
            v-model="selectedKb"
            aria-label="来源知识库"
            @change="chooseKb"
            ><el-option
              v-for="kb in kbs"
              :key="kb.id"
              :label="kb.name"
              :value="kb.id" /></el-select
        ></el-form-item>
        <el-form-item label="制度文档"
          ><el-select
            v-model="selectedDoc"
            aria-label="制度文档"
            :disabled="!selectedKb || sourceBusy"
            @change="chooseDoc"
            ><el-option
              v-for="doc in docs"
              :key="doc.id"
              :label="doc.title"
              :value="doc.id" /></el-select
        ></el-form-item>
      </div>
      <el-form-item label="原文片段"
        ><el-select
          v-model="form.source_chunk_id"
          aria-label="原文片段"
          @change="chooseChunk"
          ><el-option
            v-for="(chunk, index) in chunks"
            :key="chunk.chunk_id"
            :label="`${index + 1}. ${chunk.anchor.heading || chunk.content.slice(0, 45)}`"
            :value="chunk.chunk_id" /></el-select
      ></el-form-item>
      <pre v-if="selectedChunk" class="rule-source">{{
        selectedChunk.content
      }}</pre>
      <el-form-item label="依据摘录（必须保持原文连续文字）"
        ><el-input
          v-model="form.source_quote"
          type="textarea"
          :rows="4"
          maxlength="2000"
          aria-label="依据摘录"
      /></el-form-item> </el-form
    ><template #footer
      ><el-button @click="dialog = false">取消</el-button
      ><el-button type="primary" :loading="saving" @click="save"
        >保存草稿</el-button
      ></template
    >
  </el-dialog>
  <el-dialog
    :model-value="!!reviewing"
    title="核对原文并启用规则"
    width="min(660px,95vw)"
    @close="reviewing = null"
  >
    <template v-if="reviewing"
      ><h3>
        {{ reviewing.name }} · ¥{{ reviewing.unit_rate }} /
        {{ units[reviewing.kind] }}
      </h3>
      <p>
        {{ scopeLabel(reviewing) }}
      </p>
      <pre class="rule-source">{{ reviewing.source?.content }}</pre>
      <blockquote>{{ reviewing.source_quote }}</blockquote>
      <el-checkbox v-model="checked"
        >我已核对原文、适用范围、单位上限、比例、数量及金额上限一致</el-checkbox
      ></template
    >
    <template #footer
      ><el-button @click="reviewing = null">取消</el-button
      ><el-button
        type="primary"
        :disabled="!checked"
        :loading="saving"
        @click="reviewing && transition(reviewing, true)"
        >确认启用</el-button
      ></template
    >
  </el-dialog>
  <el-dialog
    :model-value="!!recovery"
    title="核对当前制度并恢复规则"
    width="min(850px,96vw)"
    :close-on-click-modal="false"
    @close="recovery = null"
  >
    <template v-if="recovery"
      ><h3>{{ recovery.rule.name }}</h3>
      <p>
        {{ scopeLabel(recovery.rule) }}，原单价{{ recovery.rule.unit_rate }}元。
      </p>
      <el-alert
        v-if="!recovery.current_version_id"
        title="当前没有可用制度版本。请先完成文档处理或恢复知识库；若改用其他制度，请通过编辑重新关联。"
        type="warning"
        :closable="false"
      />
      <div class="recovery-columns">
        <section>
          <h4>原依据 · v{{ recovery.previous_source.version_no }}</h4>
          <pre class="rule-source">{{ recovery.previous_source.quote }}</pre>
        </section>
        <section>
          <h4>选择当前有效依据</h4>
          <el-select
            v-model="recoverySource"
            data-testid="recovery-source-select"
            aria-label="恢复规则来源片段"
            :disabled="!recovery.current_version_id"
            @change="selectRecoverySource"
            ><el-option
              v-for="(source, index) in recovery.candidates"
              :key="source.chunk_id"
              :label="`${index + 1}. ${source.content.slice(0, 85)}`"
              :value="source.chunk_id"
          /></el-select>
          <pre v-if="candidate" class="rule-source">{{
            candidate.content
          }}</pre>
        </section>
      </div>
      <el-form label-position="top"
        ><el-form-item label="核对后的单价（元）"
          ><el-input
            v-model="recoveryRate"
            aria-label="恢复规则单价" /></el-form-item
        ><el-form-item label="当前条款摘录"
          ><el-input
            v-model="recoveryQuote"
            type="textarea"
            :rows="3"
            maxlength="2000"
            aria-label="恢复规则摘录" /></el-form-item
      ></el-form>
      <el-checkbox v-model="recoveryChecked" :disabled="!candidate"
        >我已核对新条款、适用范围、单位及单价一致</el-checkbox
      >
      <p class="subtle">
        确认后仅恢复这一条规则；未处理的关联规则继续暂停。已有旧预算不会自动更新。
      </p> </template
    ><template #footer
      ><el-button @click="recovery = null">取消</el-button
      ><el-button
        type="primary"
        :disabled="!candidate || !recoveryChecked"
        :loading="saving"
        @click="recoverRule"
        >确认恢复规则</el-button
      ></template
    >
  </el-dialog>
  <el-drawer
    :model-value="!!history"
    :title="history ? history.name + ' · 操作历史' : '规则操作历史'"
    size="min(1000px,97vw)"
    @close="history = null"
    ><AuditHistory
      v-if="history"
      :key="history.id"
      entity-type="rule"
      :entity-id="history.id"
  /></el-drawer>
</template>
