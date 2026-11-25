import { createRouter, createWebHistory } from "vue-router";
import { useAuth } from "./stores/auth";
import LoginView from "./views/LoginView.vue";
import AppLayout from "./views/AppLayout.vue";
import WorkspaceView from "./views/ChatView.vue";
import AccountView from "./views/AccountView.vue";
import UsersView from "./views/UsersView.vue";
import KnowledgeBasesView from "./views/KnowledgeBasesView.vue";
import ProfilesView from "./views/ProfilesView.vue";
import DocumentsView from "./views/DocumentsView.vue";
import FeeRulesView from "./views/FeeRulesView.vue";
import AuditLogView from "./views/AuditLogView.vue";
import UsageView from "./views/UsageView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/login", component: LoginView },
    {
      path: "/",
      component: AppLayout,
      children: [
        {
          path: "admin/audit",
          component: AuditLogView,
          meta: { title: "操作历史", admin: true },
        },
        {
          path: "admin/usage",
          component: UsageView,
          meta: { title: "用量与保护", admin: true },
        },
        {
          path: "admin/fee-rules",
          component: FeeRulesView,
          meta: { title: "费用规则", admin: true },
        },
        {
          path: "chat/:id",
          component: WorkspaceView,
          meta: { title: "知识问答" },
        },
        {
          path: "knowledge",
          component: DocumentsView,
          meta: { title: "文档检索" },
        },
        {
          path: "",
          name: "workspace",
          component: WorkspaceView,
          meta: { title: "知识问答" },
        },
        {
          path: "account",
          component: AccountView,
          meta: { title: "个人资料与安全" },
        },
        {
          path: "admin/users",
          component: UsersView,
          meta: { title: "账号管理", admin: true },
        },
        {
          path: "admin/knowledge-bases",
          component: KnowledgeBasesView,
          meta: { title: "知识库管理", admin: true },
        },
        {
          path: "admin/profiles",
          component: ProfilesView,
          meta: { title: "员工资料", admin: true },
        },
      ],
    },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});
router.beforeEach(async (to) => {
  const auth = useAuth();
  await auth.init();
  if (!auth.user) return to.path === "/login" ? true : "/login";
  if (auth.user.must_change_password && to.path !== "/account")
    return "/account";
  if (to.path === "/login") return "/";
  if (to.meta.admin && !auth.admin) return "/";
  document.title = `${to.meta.title || "登录"} · 企业知识库`;
  return true;
});
