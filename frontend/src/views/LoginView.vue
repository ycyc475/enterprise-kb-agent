<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { useAuth } from "../stores/auth";
import { errorMessage } from "../api";
const auth = useAuth(),
  router = useRouter();
const username = ref(""),
  password = ref(""),
  busy = ref(false),
  error = ref("");
async function login() {
  if (!username.value.trim() || !password.value) {
    error.value = "请输入账号和密码";
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    await auth.login(username.value.trim(), password.value);
    password.value = "";
    await router.push(auth.user?.must_change_password ? "/account" : "/");
  } catch (e) {
    error.value = errorMessage(e);
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="login-page">
    <section class="login-story">
      <div class="brand">
        <img src="/favicon.svg" alt="" /><span>企业知识库</span>
      </div>
      <div class="story-content">
        <span class="eyebrow">KNOWLEDGE, CONNECTED.</span>
        <h1>让知识流动，<br />让工作有据可循。</h1>
        <p>
          连接企业文档与每一次提问，<br />在清晰的权限边界内，找到可信的答案。
        </p>
        <div class="story-notes">
          <span>01　统一知识入口</span><span>02　按角色与知识库授权</span
          ><span>03　员工资料统一维护</span>
        </div>
      </div>
      <small>企业内部知识工作台 · 本地演示</small>
    </section>
    <main class="login-panel">
      <form class="login-form" @submit.prevent="login">
        <span class="eyebrow muted">WELCOME BACK</span>
        <h2>登录工作台</h2>
        <p class="subtle">使用管理员为你创建的企业账号。</p>
        <label for="username">账号</label
        ><el-input
          id="username"
          v-model="username"
          size="large"
          autocomplete="username"
          maxlength="64"
          placeholder="请输入企业账号"
          :disabled="busy"
        />
        <label for="password">密码</label
        ><el-input
          id="password"
          v-model="password"
          type="password"
          show-password
          size="large"
          autocomplete="current-password"
          maxlength="128"
          placeholder="请输入密码"
          :disabled="busy"
        />
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <el-button
          class="login-submit"
          type="primary"
          native-type="submit"
          size="large"
          :loading="busy"
          >登录</el-button
        >
        <div class="login-help">
          暂不开放自主注册。忘记密码请联系管理员重置。
        </div>
      </form>
    </main>
  </div>
</template>
