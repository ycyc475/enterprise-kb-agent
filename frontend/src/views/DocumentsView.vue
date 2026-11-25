<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from "vue";
import { useRoute } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, get, errorMessage } from "../api";
import { useAuth } from "../stores/auth";
import type { KnowledgeBase, Page } from "../types";
import AuditHistory from "../components/AuditHistory.vue";

interface Version {
  id: string;
  version_no: number;
  original_name: string;
  format: string;
  status: string;
  effective: boolean;
  byte_size: number;
  extracted_chars: number;
}
interface Job {
  id: string;
  status: string;
  error_message: string | null;
  attempt_no: number;
}
interface Doc {
  id: string;
  title: string;
  current_version: Version | null;
  latest_version?: Version;
  job?: Job;
}
interface Hit {
  original_name: string;
  chunk_id: string;
  document_id: string;
  version_id: string;
  title: string;
  version_no: number;
  content: string;
  anchor: Record<string, unknown>;
  branches?: string[];
  download_path: string;
}
interface Usage {
  estimated_cost_cny: string;
  requests: number;
  warning: boolean;
  paused: boolean;
  limit_cny: string;
}
const auth = useAuth(),
  route = useRoute();
const kbs = ref<KnowledgeBase[]>([]),
  selected = ref(""),
  docs = ref<Doc[]>([]),
  loading = ref(false);
const query = ref(""),
  searching = ref(false),
  hits = ref<Hit[]>([]),
  searched = ref(false),
  searchError = ref("");
const dialog = ref(false),
  replacing = ref<Doc | null>(null),
  title = ref(""),
  chosen = ref<File | null>(null),
  uploading = ref(false);
const starts = ref(""),
  ends = ref(""),
  inputKey = ref(0);
const drawer = ref(false),
  sources = ref<Hit[]>([]),
  sourceLoading = ref(false),
  sourceTitle = ref("");
const usage = ref<Usage | null>(null);
const historyDoc = ref<Doc | null>(null);
const currentKb = computed(() =>
  kbs.value.find((k) => k.id === selected.value),
);
const pending = computed(() =>
  docs.value.some((d) =>
    ["queued", "processing"].includes(d.job?.status || ""),
  ),
);
const statusLabel: Record<string, string> = {
  queued: "排队中",
  processing: "处理中",
  ready: "已就绪",
  failed: "处理失败",
  completed: "已完成",
};
let timer: ReturnType<typeof setInterval> | undefined;
let alive = true;
function location(anchor: Record<string, unknown>) {
  const pieces = [];
  if (anchor.page) pieces.push(`第${anchor.page}页`);
  if (anchor.heading) pieces.push(String(anchor.heading));
  if (anchor.line_start)
    pieces.push(`第${anchor.line_start}—${anchor.line_end}行`);
  if (anchor.paragraph) pieces.push(`第${anchor.paragraph}段`);
  if (anchor.table) pieces.push(`表${anchor.table} / 第${anchor.row}行`);
  return pieces.join(" · ") || "正文";
}
async function load(silent = false) {
  if (!selected.value) {
    docs.value = [];
    return;
  }
  const scope = selected.value;
  if (!silent) loading.value = true;
  try {
    const result = await get<Page<Doc>>(`/knowledge-bases/${scope}/documents`);
    if (scope === selected.value && alive) docs.value = result.items;
    if (auth.admin) usage.value = await get<Usage>("/admin/usage");
  } catch (e) {
    if (!silent) ElMessage.error(errorMessage(e));
  } finally {
    if (scope === selected.value) loading.value = false;
  }
}
async function search() {
  if (!query.value.trim() || !selected.value || searching.value) return;
  searching.value = true;
  searched.value = false;
  hits.value = [];
  searchError.value = "";
  const scope = selected.value;
  try {
    const response = await api.post(
      "/search",
      { query: query.value.trim(), kb_ids: [scope] },
      { timeout: 60000 },
    );
    if (selected.value === scope) {
      hits.value = response.data.data.items;
      searched.value = true;
    }
  } catch (e) {
    searchError.value = errorMessage(e);
  } finally {
    searching.value = false;
    if (auth.admin) await load(true);
  }
}
function openUpload(doc: Doc | null = null) {
  replacing.value = doc;
  title.value = doc?.title || "";
  chosen.value = null;
  starts.value = "";
  ends.value = "";
  inputKey.value++;
  dialog.value = true;
}
function choose(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0];
  chosen.value = file || null;
  if (file && !title.value) title.value = file.name.replace(/\.[^.]+$/, "");
}
async function upload() {
  if (!chosen.value || !title.value.trim()) {
    ElMessage.warning("请选择文件并填写标题");
    return;
  }
  if (chosen.value.size > 10000000) {
    ElMessage.warning("单文件不能超过10MB");
    return;
  }
  if (
    starts.value &&
    ends.value &&
    new Date(ends.value) <= new Date(starts.value)
  ) {
    ElMessage.warning("失效时间必须晚于生效时间");
    return;
  }
  const body = new FormData();
  body.append("file", chosen.value);
  if (starts.value)
    body.append("effective_from", new Date(starts.value).toISOString());
  if (ends.value)
    body.append("effective_until", new Date(ends.value).toISOString());
  if (replacing.value?.current_version)
    body.append(
      "expected_current_version_id",
      replacing.value.current_version.id,
    );
  if (!replacing.value) body.append("title", title.value.trim());
  uploading.value = true;
  try {
    await api.post(
      replacing.value
        ? `/admin/documents/${replacing.value.id}/versions`
        : `/admin/knowledge-bases/${selected.value}/documents`,
      body,
      { timeout: 60000 },
    );
    dialog.value = false;
    ElMessage.success("文件已接收，正在后台解析和建立索引");
    await load();
  } catch (e) {
    ElMessage.error(errorMessage(e));
  } finally {
    uploading.value = false;
  }
}
async function retry(doc: Doc) {
  try {
    await api.post(`/admin/ingestion-jobs/${doc.job!.id}/retry`);
    ElMessage.success("已重新排队");
    await load();
  } catch (e) {
    ElMessage.error(errorMessage(e));
  }
}
async function remove(doc: Doc) {
  try {
    await ElMessageBox.confirm(
      `确认删除“${doc.title}”？删除后将立即停止检索和下载。`,
      "删除文档",
      { type: "warning", confirmButtonText: "删除文档" },
    );
  } catch {
    return;
  }
  try {
    await api.delete(`/admin/documents/${doc.id}`);
    hits.value = [];
    searched.value = false;
    drawer.value = false;
    sources.value = [];
    await load();
    ElMessage.success("文档已删除");
  } catch (e) {
    ElMessage.error(errorMessage(e));
  }
}
async function preview(doc: Doc) {
  const version =
    doc.latest_version?.status === "ready"
      ? doc.latest_version
      : doc.current_version;
  if (!version) return;
  sources.value = [];
  drawer.value = true;
  sourceLoading.value = true;
  sourceTitle.value = `${doc.title} · v${version.version_no}`;
  try {
    sources.value = (
      await get<Page<Hit>>(`/admin/document-versions/${version.id}/chunks`)
    ).items;
  } catch (e) {
    drawer.value = false;
    ElMessage.error(errorMessage(e));
  } finally {
    sourceLoading.value = false;
  }
}
async function citation(hit: Hit) {
  sources.value = [];
  drawer.value = true;
  sourceLoading.value = true;
  sourceTitle.value = hit.title;
  try {
    sources.value = [await get<Hit>(`/document-chunks/${hit.chunk_id}`)];
  } catch (e) {
    drawer.value = false;
    ElMessage.error(errorMessage(e));
  } finally {
    sourceLoading.value = false;
  }
}
async function download(path: string, name: string) {
  try {
    const response = await api.get(path, { responseType: "blob" });
    const url = URL.createObjectURL(response.data),
      link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    ElMessage.error(errorMessage(e));
  }
}
watch(selected, () => {
  hits.value = [];
  searched.value = false;
  searchError.value = "";
  drawer.value = false;
  sources.value = [];
  void load();
});
onMounted(async () => {
  try {
    kbs.value = (
      await get<Page<KnowledgeBase>>(
        auth.admin
          ? "/knowledge-bases?include_inactive=true"
          : "/knowledge-bases",
      )
    ).items;
    const requested = String(route.query.kb || "");
    selected.value = kbs.value.some((k) => k.id === requested)
      ? requested
      : kbs.value[0]?.id || "";
  } catch (e) {
    ElMessage.error(errorMessage(e));
  }
  timer = setInterval(() => {
    if (pending.value && auth.admin) void load(true);
  }, 1500);
});
onUnmounted(() => {
  alive = false;
  if (timer) clearInterval(timer);
});
</script>

<template>
  <div class="page-heading">
    <div>
      <span class="eyebrow muted">DOCUMENTS & EVIDENCE</span>
      <h1>文档检索</h1>
      <p>检索授权文档，查看原文与位置，让依据清晰可见。</p>
    </div>
    <el-button
      v-if="auth.admin"
      type="primary"
      :disabled="!selected"
      @click="openUpload()"
      >＋ 上传文档</el-button
    >
  </div>
  <el-alert
    v-if="usage?.warning || usage?.paused"
    class="space-bottom"
    :type="usage.paused ? 'error' : 'warning'"
    :closable="false"
    :title="
      usage.paused
        ? '已达到应用预算上限，暂停新增向量请求'
        : '应用估算用量已达到提醒线，请核对百炼账单'
    "
    show-icon
  />
  <section class="panel document-search">
    <div class="document-scope">
      <label>知识库</label
      ><el-select
        v-model="selected"
        aria-label="文档知识库"
        placeholder="暂无授权知识库"
        style="width: 260px"
        :disabled="searching || uploading"
        ><el-option
          v-for="kb in kbs"
          :key="kb.id"
          :label="kb.name + (kb.is_active ? '' : '（已停用）')"
          :value="kb.id" /></el-select
      ><span v-if="usage" class="subtle"
        >模型用量估算 ¥{{ Number(usage.estimated_cost_cny).toFixed(6) }} / ¥{{
          usage.limit_cny
        }}</span
      >
    </div>
    <form class="search-form" @submit.prevent="search">
      <el-input
        v-model="query"
        aria-label="检索问题"
        placeholder="例如：北京出差的住宿标准是多少？"
        maxlength="300"
        size="large"
        clearable
        :disabled="!selected || searching"
      /><el-button
        type="primary"
        size="large"
        native-type="submit"
        :loading="searching"
        :disabled="!selected || !query.trim() || !currentKb?.is_active"
        >检索文档</el-button
      >
    </form>
    <p class="subtle">
      结合关键词与语义检索，返回原文片段。可前往“知识问答”获取综合回答并继续追问。
    </p>
  </section>
  <el-alert
    v-if="searchError"
    class="space-bottom"
    :title="searchError"
    type="error"
    :closable="false"
    show-icon
  />
  <section v-if="searched" class="search-results">
    <div class="results-heading">
      <h2>
        检索结果 <span class="inline-count">{{ hits.length }}</span>
      </h2>
      <span class="subtle">按综合排名展示 · 点击来源重新核验访问权限</span>
    </div>
    <el-empty
      v-if="!hits.length"
      description="没有找到可检索片段，请确认已有生效且处理成功的文档，或调整关键词"
    />
    <article
      v-for="(hit, index) in hits"
      :key="hit.chunk_id"
      class="panel result-card"
    >
      <div class="result-meta">
        <span class="result-number">{{
          String(index + 1).padStart(2, "0")
        }}</span
        ><strong>{{ hit.title }}</strong
        ><el-tag size="small" effect="plain">v{{ hit.version_no }}</el-tag
        ><span class="subtle">{{ location(hit.anchor) }}</span>
      </div>
      <pre>{{ hit.content }}</pre>
      <div class="result-actions">
        <span class="subtle">{{
          hit.branches
            ?.map((b) => (b === "keyword" ? "关键词命中" : "语义召回"))
            .join(" · ")
        }}</span
        ><el-button link type="primary" @click="citation(hit)"
          >查看来源</el-button
        >
      </div>
    </article>
  </section>
  <section class="panel table-panel">
    <div class="table-heading">
      <h2>
        文档列表 <span class="inline-count">{{ docs.length }}</span>
      </h2>
      <el-button :loading="loading" @click="load()">刷新</el-button>
    </div>
    <el-empty
      v-if="!selected"
      description="暂无可访问知识库，请联系管理员授权"
    /><el-table
      v-else
      v-loading="loading"
      :data="docs"
      empty-text="暂无文档；管理员可上传文件建立索引"
      ><el-table-column label="文档" min-width="210"
        ><template #default="{ row }"
          ><strong>{{ row.title }}</strong>
          <div class="cell-subtle">
            {{ (row.latest_version || row.current_version)?.original_name }}
          </div></template
        ></el-table-column
      ><el-table-column label="当前版本" width="105"
        ><template #default="{ row }"
          >{{
            row.current_version
              ? "v" + row.current_version.version_no
              : "未发布"
          }}
          <div
            v-if="row.current_version && !row.current_version.effective"
            class="cell-subtle"
          >
            当前不在有效期
          </div></template
        ></el-table-column
      ><el-table-column label="处理状态" min-width="180"
        ><template #default="{ row }"
          ><el-tag
            :type="
              row.latest_version?.status === 'failed'
                ? 'danger'
                : row.latest_version?.status === 'ready' || !auth.admin
                  ? 'success'
                  : 'warning'
            "
            effect="plain"
            >{{
              statusLabel[
                (row.latest_version || row.current_version)?.status
              ] || "等待处理"
            }}</el-tag
          >
          <div v-if="row.job?.error_message" class="job-error">
            {{ row.job.error_message }}
          </div></template
        ></el-table-column
      ><el-table-column label="操作" :width="auth.admin ? 440 : 100"
        ><template #default="{ row }"
          ><el-button
            v-if="row.current_version"
            link
            type="primary"
            @click="
              download(
                `/documents/${row.id}/versions/${row.current_version.id}/download`,
                row.current_version.original_name,
              )
            "
            >下载</el-button
          ><template v-if="auth.admin"
            ><el-button
              v-if="
                row.current_version || row.latest_version?.status === 'ready'
              "
              link
              type="primary"
              @click="preview(row)"
              >核对片段</el-button
            ><el-button
              v-if="row.job?.status === 'failed'"
              link
              type="primary"
              @click="retry(row)"
              >重试</el-button
            ><el-button
              link
              type="primary"
              :disabled="['queued', 'processing'].includes(row.job?.status)"
              @click="openUpload(row)"
              >替换</el-button
            ><el-button link type="danger" @click="remove(row)">删除</el-button
            ><el-button link @click="historyDoc = row">操作历史</el-button
            ><router-link
              class="text-link"
              :to="'/admin/fee-rules?document_id=' + row.id"
              >关联规则</router-link
            ></template
          ></template
        ></el-table-column
      ></el-table
    >
    <p class="table-footnote">
      {{
        auth.admin
          ? "支持MD、文字PDF、DOCX；单文件10MB、PDF100页、正文10万字符，最多30份当前文档。新版本全部处理成功后发布。"
          : "仅展示当前获授权、已生效且可用的文档。"
      }}
    </p>
  </section>
  <p v-if="auth.admin" class="subtle">
    制度新版本发布后，受影响规则会暂停；可从“关联规则”入口核对新依据并恢复。
  </p>
  <el-drawer
    v-if="auth.admin && historyDoc"
    :model-value="!!historyDoc"
    :title="historyDoc?.title + ' · 操作历史'"
    size="min(1000px,97vw)"
    @close="historyDoc = null"
    ><AuditHistory
      v-if="historyDoc"
      :key="historyDoc.id"
      entity-type="document"
      :entity-id="historyDoc.id"
  /></el-drawer>
  <el-dialog
    v-model="dialog"
    :title="replacing ? '替换文档版本' : '上传文档'"
    width="530px"
    :close-on-click-modal="false"
    :show-close="!uploading"
    :close-on-press-escape="!uploading"
    ><form @submit.prevent="upload">
      <div class="field">
        <label for="document-title">文档标题</label
        ><el-input
          id="document-title"
          v-model="title"
          maxlength="200"
          :disabled="!!replacing || uploading"
        />
      </div>
      <div class="field">
        <label for="document-file">选择文件</label
        ><input
          id="document-file"
          :key="inputKey"
          type="file"
          accept=".md,.pdf,.docx"
          :disabled="uploading"
          @change="choose"
        />
        <p class="subtle">文字PDF无需OCR；不支持扫描件和复杂图表。</p>
      </div>
      <div class="field-row">
        <div class="field">
          <label for="effective-start">生效时间（可选）</label
          ><input
            id="effective-start"
            v-model="starts"
            class="date-input"
            type="datetime-local"
            :disabled="uploading"
          />
        </div>
        <div class="field">
          <label for="effective-end">失效时间（可选）</label
          ><input
            id="effective-end"
            v-model="ends"
            class="date-input"
            type="datetime-local"
            :disabled="uploading"
          />
        </div>
      </div>
      <p class="subtle">
        未填写时立即生效、长期有效。时间按本机时区输入；失效时间为不包含的上界。
      </p>
      <div class="dialog-actions">
        <el-button :disabled="uploading" @click="dialog = false">取消</el-button
        ><el-button type="primary" native-type="submit" :loading="uploading"
          >上传并建立索引</el-button
        >
      </div>
    </form></el-dialog
  >
  <el-drawer v-model="drawer" :title="sourceTitle" size="min(620px, 95vw)"
    ><div v-loading="sourceLoading">
      <article
        v-for="source in sources"
        :key="source.chunk_id"
        class="source-block"
      >
        <div class="subtle">
          v{{ source.version_no }} · {{ location(source.anchor) }}
        </div>
        <pre>{{ source.content }}</pre>
        <el-button
          link
          type="primary"
          @click="download(source.download_path, source.original_name)"
          >下载原文件</el-button
        >
      </article>
      <el-empty
        v-if="!sourceLoading && !sources.length"
        description="暂无可核对片段"
      /></div
  ></el-drawer>
</template>
