<script setup lang="ts">
import { ref, reactive, onMounted } from "vue";
import { ElMessage } from "element-plus";
import { get, post, patch, put, errorMessage } from "../api";
import type { KnowledgeBase, User, Page } from "../types";
const kbs = ref<KnowledgeBase[]>([]),
  users = ref<User[]>([]),
  loading = ref(false),
  saving = ref(false);
const dialog = ref(false),
  editing = ref<KnowledgeBase | null>(null),
  memberKb = ref<KnowledgeBase | null>(null),
  memberIds = ref<string[]>([]),
  memberRevision = ref(1);
const form = reactive({ name: "", description: "", is_active: true });
async function load() {
  loading.value = true;
  try {
    const [bases, people] = await Promise.all([
      get<Page<KnowledgeBase>>("/knowledge-bases?include_inactive=true"),
      get<Page<User>>("/admin/users"),
    ]);
    kbs.value = bases.items;
    users.value = people.items.filter((u) => u.role === "employee");
  } catch (e) {
    ElMessage.error(errorMessage(e));
  } finally {
    loading.value = false;
  }
}
function open(kb: KnowledgeBase | null) {
  editing.value = kb;
  Object.assign(form, kb || { name: "", description: "", is_active: true });
  dialog.value = true;
}
async function save() {
  if (!form.name.trim()) {
    ElMessage.warning("请输入知识库名称");
    return;
  }
  saving.value = true;
  try {
    const body = {
      name: form.name,
      description: form.description,
      is_active: form.is_active,
    };
    if (editing.value)
      await patch(`/admin/knowledge-bases/${editing.value.id}`, {
        ...body,
        expected_revision: editing.value.revision,
      });
    else await post("/admin/knowledge-bases", body);
    dialog.value = false;
    ElMessage.success("知识库已保存");
    await load();
  } catch (e) {
    ElMessage.error(errorMessage(e));
  } finally {
    saving.value = false;
  }
}
async function editMembers(kb: KnowledgeBase) {
  try {
    const data = await get<{ user_ids: string[]; revision: number }>(
      `/admin/knowledge-bases/${kb.id}/members`,
    );
    memberIds.value = data.user_ids;
    memberRevision.value = data.revision;
    memberKb.value = kb;
  } catch (e) {
    ElMessage.error(errorMessage(e));
  }
}
async function saveMembers() {
  if (!memberKb.value) return;
  saving.value = true;
  try {
    await put(`/admin/knowledge-bases/${memberKb.value.id}/members`, {
      user_ids: memberIds.value,
      expected_revision: memberRevision.value,
    });
    memberKb.value = null;
    ElMessage.success("授权范围已更新");
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
      <span class="eyebrow muted">KNOWLEDGE SPACES</span>
      <h1>知识库管理</h1>
      <p>按业务划分知识空间，为合适的员工开放访问。</p>
    </div>
    <el-button type="primary" @click="open(null)">＋ 创建知识库</el-button>
  </div>
  <section class="panel table-panel">
    <div class="table-heading">
      <h2>
        企业知识库 <span class="inline-count">{{ kbs.length }}</span>
      </h2>
      <el-button :loading="loading" @click="load">刷新</el-button>
    </div>
    <el-table v-loading="loading" :data="kbs" empty-text="尚未创建知识库"
      ><el-table-column label="知识库" min-width="230"
        ><template #default="{ row }"
          ><div class="kb-table-name">
            <span class="kb-icon">▤</span><strong>{{ row.name }}</strong>
          </div></template
        ></el-table-column
      ><el-table-column
        prop="description"
        label="说明"
        min-width="240"
        show-overflow-tooltip
      /><el-table-column label="状态" width="110"
        ><template #default="{ row }"
          ><el-tag :type="row.is_active ? 'success' : 'info'" effect="plain">{{
            row.is_active ? "已启用" : "已停用"
          }}</el-tag></template
        ></el-table-column
      ><el-table-column label="操作" width="260"
        ><template #default="{ row }"
          ><router-link
            :to="{ path: '/knowledge', query: { kb: row.id } }"
            class="text-link-inline"
            >文档</router-link
          ><el-button link type="primary" @click="open(row)">编辑</el-button
          ><el-button link type="primary" @click="editMembers(row)"
            >管理授权</el-button
          ></template
        ></el-table-column
      ></el-table
    >
    <p class="table-footnote">
      管理员可管理全部知识库。员工仅能访问已启用且已获授权的知识库，点击“文档”管理文件与检索。
    </p>
  </section>
  <el-dialog
    v-model="dialog"
    :title="editing ? '编辑知识库' : '创建知识库'"
    width="480px"
    :close-on-click-modal="false"
    ><form @submit.prevent="save">
      <div class="field">
        <label for="kb-name">知识库名称</label
        ><el-input id="kb-name" v-model="form.name" maxlength="80" />
      </div>
      <div class="field">
        <label for="kb-description">说明</label
        ><el-input
          id="kb-description"
          v-model="form.description"
          type="textarea"
          :rows="3"
          maxlength="1000"
          show-word-limit
        />
      </div>
      <div class="field">
        <label>访问状态</label
        ><el-switch
          v-model="form.is_active"
          active-text="启用"
          inactive-text="停用"
        />
        <p class="subtle">停用后，员工将无法访问此知识库。</p>
      </div>
      <div class="dialog-actions">
        <el-button @click="dialog = false">取消</el-button
        ><el-button type="primary" native-type="submit" :loading="saving"
          >保存知识库</el-button
        >
      </div>
    </form></el-dialog
  >
  <el-dialog
    :model-value="!!memberKb"
    title="管理知识库授权"
    width="500px"
    :close-on-click-modal="false"
    @update:model-value="
      (value: boolean) => {
        if (!value) memberKb = null;
      }
    "
    ><h3>{{ memberKb?.name }}</h3>
    <p class="subtle">
      选择允许访问此知识库的员工。取消勾选并保存后立即撤销授权。
    </p>
    <el-checkbox-group v-model="memberIds" class="member-list"
      ><el-checkbox v-for="user in users" :key="user.id" :value="user.id"
        ><strong>{{ user.profile?.display_name || user.username }}</strong
        ><span
          >{{ user.profile?.department }} · {{ user.username
          }}{{ user.is_active ? "" : " · 已禁用" }}</span
        ></el-checkbox
      ></el-checkbox-group
    >
    <p v-if="!users.length" class="subtle">尚无员工账号，请先创建。</p>
    <template #footer
      ><el-button @click="memberKb = null">取消</el-button
      ><el-button type="primary" :loading="saving" @click="saveMembers"
        >保存授权</el-button
      ></template
    ></el-dialog
  >
</template>
