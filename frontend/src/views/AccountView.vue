<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { useAuth } from "../stores/auth";
import { post, errorMessage } from "../api";
const auth = useAuth(),
  router = useRouter(),
  oldPassword = ref(""),
  newPassword = ref(""),
  confirm = ref(""),
  busy = ref(false);
onMounted(async () => {
  try {
    await auth.loadMe();
  } catch (e) {
    ElMessage.error(errorMessage(e));
  }
});
async function changePassword() {
  if (newPassword.value.length < 12 || newPassword.value.length > 128) {
    ElMessage.warning("新密码应为12至128个字符");
    return;
  }
  if (newPassword.value !== confirm.value) {
    ElMessage.warning("两次输入的新密码不一致");
    return;
  }
  busy.value = true;
  try {
    await post("/auth/password", {
      old_password: oldPassword.value,
      new_password: newPassword.value,
    });
    auth.clear();
    oldPassword.value = newPassword.value = confirm.value = "";
    ElMessage.success("密码已修改，请重新登录");
    await router.push("/login");
  } catch (e) {
    ElMessage.error(errorMessage(e));
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="page-heading">
    <div>
      <span class="eyebrow muted">PROFILE & SECURITY</span>
      <h1>个人资料与安全</h1>
      <p>核对员工信息，管理你的登录密码。</p>
    </div>
  </div>
  <el-alert
    v-if="auth.user?.must_change_password"
    class="space-bottom"
    title="你正在使用临时密码，请先设置新密码后继续使用工作台。"
    type="warning"
    :closable="false"
    show-icon
  />
  <div class="two-column">
    <section class="panel form-panel">
      <h2>员工资料</h2>
      <p class="subtle">
        姓名、部门与职级由管理员统一维护。如有变化，请联系管理员更新。
      </p>
      <dl class="profile-details">
        <div>
          <dt>账号</dt>
          <dd>{{ auth.user?.username }}</dd>
        </div>
        <div>
          <dt>姓名</dt>
          <dd>{{ auth.user?.profile?.display_name || "—" }}</dd>
        </div>
        <div>
          <dt>部门</dt>
          <dd>{{ auth.user?.profile?.department || "—" }}</dd>
        </div>
        <div>
          <dt>职级</dt>
          <dd>{{ auth.user?.profile?.grade || "—" }}</dd>
        </div>
        <div>
          <dt>角色</dt>
          <dd>{{ auth.admin ? "管理员" : "员工" }}</dd>
        </div>
      </dl>
    </section>
    <section class="panel form-panel">
      <h2>修改密码</h2>
      <p class="subtle">修改成功后，所有设备上的登录状态都会失效。</p>
      <form @submit.prevent="changePassword">
        <div class="field">
          <label for="old-password">原密码</label
          ><el-input
            id="old-password"
            v-model="oldPassword"
            type="password"
            show-password
            autocomplete="current-password"
            maxlength="128"
            required
          />
        </div>
        <div class="field">
          <label for="new-password">新密码</label
          ><el-input
            id="new-password"
            v-model="newPassword"
            type="password"
            show-password
            autocomplete="new-password"
            maxlength="128"
            placeholder="12至128个字符"
            required
          />
        </div>
        <div class="field">
          <label for="confirm-password">确认新密码</label
          ><el-input
            id="confirm-password"
            v-model="confirm"
            type="password"
            show-password
            autocomplete="new-password"
            maxlength="128"
            required
          />
        </div>
        <el-button type="primary" native-type="submit" :loading="busy"
          >保存新密码</el-button
        >
      </form>
    </section>
  </div>
</template>
