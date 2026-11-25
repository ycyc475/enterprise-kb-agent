<script setup lang="ts">
import { ref, onMounted } from "vue";
import { ElMessage } from "element-plus";
import { get, errorMessage } from "../api";
interface Usage {
  estimated_cost_cny: string;
  requests: number;
  warning: boolean;
  paused: boolean;
  warning_cny: string;
  limit_cny: string;
  unknown_reserved_cny: string;
  remaining_estimated_cny: string;
  note: string;
  breakdown: {
    model: string;
    operation: string;
    status: string;
    requests: number;
    input_tokens: number;
    output_tokens: number;
    estimated_cost_cny: string;
  }[];
}
const data = ref<Usage | null>(null),
  loading = ref(false);
const status: Record<string, string> = {
  estimated: "按返回用量估算",
  reserved: "费用预留中",
  failed_unknown: "失败，费用未知",
  usage_unknown: "用量未知",
};
const operations: Record<string, string> = {
  chat: "对话模型",
  evidence_review: "回答证据核验",
  query_embedding: "检索问题向量",
  document_embedding: "文档向量",
  m6_chat: "验收对话模型",
  m6_evidence_review: "验收回答证据核验",
  m6_query_embedding: "验收检索问题向量",
  m6_document_embedding: "验收文档向量",
  m6_evaluation: "验收费用预留",
};
async function load() {
  loading.value = true;
  try {
    data.value = await get<Usage>("/admin/usage");
  } catch (e) {
    ElMessage.error(errorMessage(e));
  } finally {
    loading.value = false;
  }
}
onMounted(load);
</script>
<template>
  <div class="page-heading">
    <div>
      <span class="eyebrow muted">MODEL USAGE</span>
      <h1>用量与保护</h1>
      <p>核对本应用的模型估算与预留。实际费用和服务商额度以百炼账单为准。</p>
    </div>
    <el-button :loading="loading" @click="load">刷新用量</el-button>
  </div>
  <template v-if="data"
    ><div class="usage-cards">
      <section class="panel">
        <p class="subtle">累计估算（含未知预留）</p>
        <h2>¥{{ Number(data.estimated_cost_cny).toFixed(6) }}</h2>
        <span>{{ data.requests }}条调用/预留记录</span>
      </section>
      <section class="panel">
        <p class="subtle">费用未知及在途预留</p>
        <h2>¥{{ Number(data.unknown_reserved_cny).toFixed(6) }}</h2>
        <span>保留预算，不按零费用释放</span>
      </section>
      <section class="panel">
        <p class="subtle">本应用保护阈值</p>
        <h2>¥{{ data.warning_cny }} / ¥{{ data.limit_cny }}</h2>
        <span>{{
          data.paused
            ? "已暂停新增付费请求"
            : data.warning
              ? "已到提醒阈值"
              : "当前估算未到提醒阈值"
        }}</span>
      </section>
    </div>
    <el-alert
      class="space-bottom"
      :type="data.paused ? 'error' : data.warning ? 'warning' : 'info'"
      :closable="false"
      title="每次请求会先预留费用；即使累计未到上限，本次预留将超限时也会拒绝。无需模型的预算计算仍可使用。"
    />
    <section class="panel table-panel">
      <h2>模型与操作明细</h2>
      <el-table :data="data.breakdown"
        ><el-table-column
          prop="model"
          label="模型"
          min-width="170"
        /><el-table-column label="操作" min-width="140"
          ><template #default="{ row }">{{
            operations[row.operation] || row.operation
          }}</template></el-table-column
        ><el-table-column label="状态" min-width="150"
          ><template #default="{ row }">{{
            status[row.status] || row.status
          }}</template></el-table-column
        ><el-table-column
          prop="requests"
          label="记录数"
          width="90"
        /><el-table-column
          prop="input_tokens"
          label="已知输入Token"
          min-width="125"
        /><el-table-column
          prop="output_tokens"
          label="已知输出Token"
          min-width="125"
        /><el-table-column label="估算元" min-width="120"
          ><template #default="{ row }">{{
            Number(row.estimated_cost_cny).toFixed(8)
          }}</template></el-table-column
        ></el-table
      >
      <p class="subtle">{{ data.note }}。未知Token不包含在已知Token合计中。</p>
    </section></template
  >
</template>
