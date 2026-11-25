<script setup lang="ts">
import { ref, reactive, onMounted } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { get, post, patch, errorMessage } from "../api";
import { useAuth } from "../stores/auth";
import type { User, Page } from "../types";
const auth = useAuth(),
  users = ref<User[]>([]),
  loading = ref(false),
  saving = ref(false),
  dialog = ref(false);
const temporary = ref(""),
  issuedTo = ref("");
const form = reactive({
  username: "",
  role: "employee",
  display_name: "",
  department: "",
  grade: "S1",
});
async function load() {
  loading.value = true;
  try {
    users.value = (await get<Page<User>>("/admin/users")).items;
  } catch (e) {
    ElMessage.error(errorMessage(e));
  } finally {
    loading.value = false;
  }
}
function openCreate() {
  Object.assign(form, {
    username: "",
    role: "employee",
    display_name: "",
    department: "",
    grade: "S1",
  });
  dialog.value = true;
}
async function create() {
  if (
    !/^[a-zA-Z0-9_.-]{3,64}$/.test(form.username) ||
    !form.display_name.trim() ||
    !form.department.trim()
  ) {
    ElMessage.warning("请填写完整信息；账号应为3至64位字母、数字或 _ . -");
    return;
  }
  saving.value = true;
  try {
    const result = await post<{ user: User; temporary_password: string }>(
      "/admin/users",
      form,
    );
    dialog.value = false;
    temporary.value = result.temporary_password;
    issuedTo.value = result.user.username;
    await load();
  } catch (e) {
    ElMessage.error(errorMessage(e));
  } finally {
    saving.value = false;
  }
}
async function change(user: User, field: "is_active" | "role") {
  try {
    await ElMessageBox.confirm(
      field === "is_active"
        ? `确认${user.is_active ? "禁用" : "启用"}账号 ${user.username}？该账号已有登录状态会失效。`
        : `确认将 ${user.username} 切换为${user.role === "admin" ? "员工" : "管理员"}？已有登录状态会失效。`,
      "变更账号",
      { type: "warning", confirmButtonText: "确认变更" },
    );
  } catch {
    return;
  }
  saving.value = true;
  try {
    await patch(`/admin/users/${user.id}`, {
      [field]:
        field === "is_active"
          ? !user.is_active
          : user.role === "admin"
            ? "employee"
            : "admin",
    });
    ElMessage.success("账号已更新");
    await load();
  } catch (e) {
    ElMessage.error(errorMessage(e));
  } finally {
    saving.value = false;
  }
}
async function reset(user: User) {
  try {
    await ElMessageBox.confirm(
      `确认重置 ${user.username} 的密码？所有设备将退出登录，下次登录必须修改临时密码。`,
      "重置密码",
      { type: "warning", confirmButtonText: "重置密码" },
    );
  } catch {
    return;
  }
  saving.value = true;
  try {
    const result = await post<{ temporary_password: string }>(
      `/admin/users/${user.id}/password-reset`,
    );
    temporary.value = result.temporary_password;
    issuedTo.value = user.username;
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
      <span class="eyebrow muted">ACCESS MANAGEMENT</span>
      <h1>账号管理</h1>
      <p>管理企业账号、角色与登录权限。</p>
    </div>
    <el-button type="primary" @click="openCreate">＋ 创建账号</el-button>
  </div>
  <section class="panel table-panel">
    <div class="table-heading">
      <h2>
        企业成员 <span class="inline-count">{{ users.length }}</span>
      </h2>
      <el-button :loading="loading" @click="load">刷新</el-button>
    </div>
    <el-table v-loading="loading" :data="users" empty-text="暂无成员"
      ><el-table-column
        prop="username"
        label="账号"
        min-width="140"
      /><el-table-column label="姓名 / 部门" min-width="160"
        ><template #default="{ row }"
          ><strong>{{ row.profile?.display_name }}</strong>
          <div class="cell-subtle">{{ row.profile?.department }}</div></template
        ></el-table-column
      ><el-table-column label="角色" width="100"
        ><template #default="{ row }"
          ><el-tag
            :type="row.role === 'admin' ? 'primary' : 'info'"
            effect="plain"
            >{{ row.role === "admin" ? "管理员" : "员工" }}</el-tag
          ></template
        ></el-table-column
      ><el-table-column label="状态" min-width="130"
        ><template #default="{ row }"
          ><span :class="row.is_active ? 'state-on' : 'subtle'">{{
            row.is_active ? "● 已启用" : "● 已禁用"
          }}</span>
          <div v-if="row.must_change_password" class="cell-subtle">
            待修改临时密码
          </div></template
        ></el-table-column
      ><el-table-column label="操作" width="275"
        ><template #default="{ row }"
          ><el-button
            link
            type="primary"
            :disabled="saving || row.id === auth.user?.id"
            @click="change(row, 'is_active')"
            >{{ row.is_active ? "禁用" : "启用" }}</el-button
          ><el-button
            link
            type="primary"
            :disabled="saving || row.id === auth.user?.id"
            @click="change(row, 'role')"
            >切换角色</el-button
          ><el-button
            link
            type="primary"
            :disabled="saving || row.id === auth.user?.id"
            @click="reset(row)"
            >重置密码</el-button
          ></template
        ></el-table-column
      ></el-table
    >
    <p class="table-footnote">
      为避免失去管理入口，当前管理员不能禁用或降级自己的账号。修改自己的密码请前往“个人资料”。
    </p>
  </section>
  <el-dialog
    v-model="dialog"
    title="创建企业账号"
    width="480px"
    :close-on-click-modal="false"
    ><form @submit.prevent="create">
      <div class="field">
        <label for="create-username">账号</label
        ><el-input
          id="create-username"
          v-model="form.username"
          maxlength="64"
          placeholder="字母、数字或 _ . -，3至64位"
        />
      </div>
      <div class="field-row">
        <div class="field">
          <label for="create-name">姓名</label
          ><el-input
            id="create-name"
            v-model="form.display_name"
            maxlength="64"
          />
        </div>
        <div class="field">
          <label for="create-dept">部门</label
          ><el-input
            id="create-dept"
            v-model="form.department"
            maxlength="80"
          />
        </div>
      </div>
      <div class="field-row">
        <div class="field">
          <label>角色</label
          ><el-select v-model="form.role" aria-label="创建账号角色"
            ><el-option label="员工" value="employee" /><el-option
              label="管理员"
              value="admin"
          /></el-select>
        </div>
        <div class="field">
          <label>职级</label
          ><el-select v-model="form.grade" aria-label="创建账号职级"
            ><el-option label="S1" value="S1" /><el-option
              label="S2"
              value="S2"
          /></el-select>
        </div>
      </div>
      <p class="subtle">系统将生成临时密码。用户首次登录后需设置新密码。</p>
      <div class="dialog-actions">
        <el-button @click="dialog = false">取消</el-button
        ><el-button type="primary" native-type="submit" :loading="saving"
          >创建账号</el-button
        >
      </div>
    </form></el-dialog
  >
  <el-dialog
    :model-value="!!temporary"
    title="请保存临时密码"
    width="480px"
    :close-on-click-modal="false"
    @update:model-value="
      (value: boolean) => {
        if (!value) temporary = '';
      }
    "
    ><p>
      账号
      <strong>{{ issuedTo }}</strong>
      的临时密码仅在此处展示。请妥善交给对应员工。
    </p>
    <div class="temporary-password" data-testid="temporary-password">
      {{ temporary }}
    </div>
    <p class="subtle">
      首次登录后必须修改密码。关闭后可通过再次重置生成新密码。
    </p>
    <template #footer
      ><el-button type="primary" @click="temporary = ''"
        >我已保存</el-button
      ></template
    ></el-dialog
  >
</template>
