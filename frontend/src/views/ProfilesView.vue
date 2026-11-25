<script setup lang="ts">
import { ref, reactive, computed, onMounted } from "vue";
import { ElMessage } from "element-plus";
import { get, patch, errorMessage } from "../api";
import type { User, Page, Profile } from "../types";
const users = ref<User[]>([]),
  search = ref(""),
  loading = ref(false),
  saving = ref(false),
  editing = ref<User | null>(null);
const form = reactive({
  display_name: "",
  department: "",
  grade: "S1",
  expected_revision: 1,
});
const filtered = computed(() =>
  users.value.filter((u) =>
    [u.username, u.profile?.display_name, u.profile?.department].some((v) =>
      v?.toLowerCase().includes(search.value.toLowerCase()),
    ),
  ),
);
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
function edit(user: User) {
  if (!user.profile) return;
  Object.assign(form, user.profile, {
    expected_revision: user.profile.revision,
  });
  editing.value = user;
}
async function save() {
  if (!editing.value || !form.display_name.trim() || !form.department.trim()) {
    ElMessage.warning("请填写姓名和部门");
    return;
  }
  saving.value = true;
  try {
    await patch<Profile>(`/admin/employees/${editing.value.id}`, {
      display_name: form.display_name,
      department: form.department,
      grade: form.grade,
      expected_revision: form.expected_revision,
    });
    editing.value = null;
    ElMessage.success("员工资料已更新");
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
      <span class="eyebrow muted">EMPLOYEE DIRECTORY</span>
      <h1>员工资料</h1>
      <p>统一维护员工姓名、部门与职级，作为制度匹配的资料来源。</p>
    </div>
  </div>
  <section class="panel table-panel">
    <div class="table-heading">
      <h2>
        员工档案 <span class="inline-count">{{ users.length }}</span>
      </h2>
      <div class="toolbar-actions">
        <el-input
          v-model="search"
          aria-label="搜索员工"
          placeholder="搜索姓名、账号或部门"
          clearable
          style="width: 240px"
        /><el-button :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>
    <el-table
      v-loading="loading"
      :data="filtered"
      empty-text="没有符合条件的员工"
      ><el-table-column label="姓名" min-width="150"
        ><template #default="{ row }"
          ><strong>{{ row.profile?.display_name }}</strong></template
        ></el-table-column
      ><el-table-column
        prop="username"
        label="账号"
        min-width="150"
      /><el-table-column label="部门" min-width="150"
        ><template #default="{ row }">{{
          row.profile?.department
        }}</template></el-table-column
      ><el-table-column label="职级" width="100"
        ><template #default="{ row }"
          ><el-tag effect="plain">{{ row.profile?.grade }}</el-tag></template
        ></el-table-column
      ><el-table-column label="状态" width="110"
        ><template #default="{ row }"
          ><span :class="row.is_active ? 'state-on' : 'subtle'">{{
            row.is_active ? "已启用" : "已禁用"
          }}</span></template
        ></el-table-column
      ><el-table-column label="操作" width="120"
        ><template #default="{ row }"
          ><el-button link type="primary" @click="edit(row)"
            >编辑资料</el-button
          ></template
        ></el-table-column
      ></el-table
    >
  </section>
  <el-dialog
    :model-value="!!editing"
    title="编辑员工资料"
    width="440px"
    :close-on-click-modal="false"
    @update:model-value="
      (value: boolean) => {
        if (!value) editing = null;
      }
    "
    ><form @submit.prevent="save">
      <p class="subtle">账号：{{ editing?.username }}</p>
      <div class="field">
        <label for="profile-name">姓名</label
        ><el-input
          id="profile-name"
          v-model="form.display_name"
          maxlength="64"
        />
      </div>
      <div class="field">
        <label for="profile-department">部门</label
        ><el-input
          id="profile-department"
          v-model="form.department"
          maxlength="80"
        />
      </div>
      <div class="field">
        <label>职级</label
        ><el-select v-model="form.grade" aria-label="员工职级"
          ><el-option label="S1" value="S1" /><el-option label="S2" value="S2"
        /></el-select>
      </div>
      <div class="dialog-actions">
        <el-button @click="editing = null">取消</el-button
        ><el-button type="primary" native-type="submit" :loading="saving"
          >保存资料</el-button
        >
      </div>
    </form></el-dialog
  >
</template>
