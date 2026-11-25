<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { errorMessage } from "../api";
import { useAuth } from "../stores/auth";
const auth = useAuth(),
  route = useRoute(),
  router = useRouter(),
  busy = ref(false);
async function logout() {
  busy.value = true;
  try {
    await auth.logout();
    await router.push("/login");
  } catch (e) {
    ElMessage.error(errorMessage(e));
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="app-shell">
    <aside class="sidebar">
      <router-link class="brand" to="/"
        ><img src="/favicon.svg" alt="" /><span
          >企业知识库<small>企业知识工作台</small></span
        ></router-link
      >
      <div class="nav-caption">工作空间</div>
      <nav>
        <router-link to="/knowledge" active-class="active"
          ><span class="nav-symbol" aria-hidden="true">▤</span
          >文档检索</router-link
        >
        <router-link
          to="/"
          exact-active-class="active"
          :class="{ active: route.path.startsWith('/chat/') }"
          ><span class="nav-symbol" aria-hidden="true">◈</span
          >知识问答</router-link
        ><router-link to="/account" active-class="active"
          ><span class="nav-symbol" aria-hidden="true">◎</span
          >个人资料</router-link
        >
      </nav>
      <template v-if="auth.admin"
        ><div class="nav-caption">管理中心</div>
        <nav>
          <router-link to="/admin/fee-rules" active-class="active"
            ><span class="nav-symbol" aria-hidden="true">¥</span
            >费用规则</router-link
          >
          <router-link to="/admin/audit" active-class="active"
            ><span class="nav-symbol" aria-hidden="true">≡</span
            >操作历史</router-link
          >
          <router-link to="/admin/usage" active-class="active"
            ><span class="nav-symbol" aria-hidden="true">◷</span
            >用量与保护</router-link
          >
          <router-link to="/admin/knowledge-bases" active-class="active"
            ><span class="nav-symbol" aria-hidden="true">▤</span
            >知识库管理</router-link
          ><router-link to="/admin/users" active-class="active"
            ><span class="nav-symbol" aria-hidden="true">▦</span
            >账号管理</router-link
          ><router-link to="/admin/profiles" active-class="active"
            ><span class="nav-symbol" aria-hidden="true">▣</span
            >员工资料</router-link
          >
        </nav></template
      >
      <div class="sidebar-bottom">
        <span class="status-dot"></span>本地演示环境
        <p>知识有边界，协作有秩序。</p>
      </div>
    </aside>
    <div class="main-shell">
      <header class="topbar">
        <div>
          <span class="breadcrumb">工作台</span
          ><span class="breadcrumb"> / </span
          ><strong>{{ route.meta.title }}</strong>
        </div>
        <div class="user-menu">
          <span class="avatar">{{
            auth.user?.profile?.display_name.slice(0, 1) || "用"
          }}</span
          ><span>{{
            auth.user?.profile?.display_name || auth.user?.username
          }}</span
          ><el-tag size="small" effect="plain">{{
            auth.admin ? "管理员" : "员工"
          }}</el-tag
          ><el-button text :loading="busy" @click="logout">退出</el-button>
        </div>
      </header>
      <main class="page-content"><router-view /></main>
    </div>
  </div>
</template>
