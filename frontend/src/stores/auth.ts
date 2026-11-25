import { defineStore } from "pinia";
import { ref, computed } from "vue";
import {
  get,
  onExpired,
  refreshSession,
  setToken,
  signIn,
  signOut,
} from "../api";
import type { User } from "../types";

export const useAuth = defineStore("auth", () => {
  const user = ref<User | null>(null);
  const initialized = ref(false);
  const admin = computed(() => user.value?.role === "admin");
  onExpired(() => {
    user.value = null;
    window.location.assign("/login");
  });
  async function loadMe() {
    user.value = await get<User>("/auth/me");
  }
  async function init() {
    if (initialized.value) return;
    try {
      if (await refreshSession()) await loadMe();
    } catch {
      clear();
    } finally {
      initialized.value = true;
    }
  }
  async function login(username: string, password: string) {
    const data = await signIn(username, password);
    user.value = data.user || null;
    initialized.value = true;
  }
  async function logout() {
    await signOut();
    clear();
  }
  function clear() {
    setToken("");
    user.value = null;
  }
  return { user, initialized, admin, loadMe, init, login, logout, clear };
});
