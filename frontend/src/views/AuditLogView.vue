<script setup lang="ts">
import AuditHistory from "../components/AuditHistory.vue";
</script>
<template>
  <div class="page-heading">
    <div>
      <span class="eyebrow muted">MANAGEMENT HISTORY</span>
      <h1>操作历史</h1>
      <p>
        核对文档、规则与授权等管理变更。成功变更与记录一同保存，仅管理员可查看。
      </p>
    </div>
  </div>
  <section class="panel table-panel"><AuditHistory /></section>
</template>
