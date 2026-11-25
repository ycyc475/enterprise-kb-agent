import axios from "axios";
import type { InternalAxiosRequestConfig } from "axios";
import type { TokenData } from "./types";

const raw = axios.create({
  baseURL: "/api/v1",
  withCredentials: true,
  timeout: 15000,
});
export const api = axios.create({
  baseURL: "/api/v1",
  withCredentials: true,
  timeout: 15000,
});
let token = "";
let refreshing: Promise<boolean> | null = null;
let expired: () => void = () => {};
export function setToken(value: string) {
  token = value;
}
export function onExpired(callback: () => void) {
  expired = callback;
}
export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error))
    return error.response?.data?.error?.message || "服务暂时不可用，请稍后重试";
  return error instanceof Error ? error.message : "操作未完成，请重试";
}
async function csrf() {
  const response = await raw.get("/auth/csrf");
  return response.data.data.csrf_token as string | null;
}
async function withAuthLock<T>(operation: () => Promise<T>): Promise<T> {
  return navigator.locks
    ? navigator.locks.request("enterprise-kb-auth", operation)
    : operation();
}
export function refreshSession(): Promise<boolean> {
  if (refreshing) return refreshing;
  refreshing = withAuthLock(async () => {
    try {
      const value = await csrf();
      if (!value) {
        token = "";
        return false;
      }
      const response = await raw.post(
        "/auth/refresh",
        {},
        { headers: { "X-CSRF-Token": value } },
      );
      token = (response.data.data as TokenData).access_token;
      return true;
    } catch {
      token = "";
      return false;
    }
  }).finally(() => {
    refreshing = null;
  });
  return refreshing;
}
export async function signIn(
  username: string,
  password: string,
): Promise<TokenData> {
  return withAuthLock(async () => {
    const response = await raw.post("/auth/login", { username, password });
    const data = response.data.data as TokenData;
    token = data.access_token;
    return data;
  });
}
export async function signOut() {
  await withAuthLock(async () => {
    const value = await csrf();
    await raw.post(
      "/auth/logout",
      {},
      { headers: value ? { "X-CSRF-Token": value } : {} },
    );
    token = "";
  });
}
api.interceptors.request.use((config) => {
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});
type RetryConfig = InternalAxiosRequestConfig & { retried?: boolean };
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const config = error.config as RetryConfig | undefined;
    if (error.response?.status === 401 && config && !config.retried) {
      config.retried = true;
      if (await refreshSession()) return api(config);
      expired();
    }
    return Promise.reject(error);
  },
);
export async function get<T>(url: string): Promise<T> {
  return (await api.get(url)).data.data as T;
}
export async function post<T>(url: string, data: unknown = {}): Promise<T> {
  return (await api.post(url, data)).data?.data as T;
}
export async function patch<T>(url: string, data: unknown): Promise<T> {
  return (await api.patch(url, data)).data.data as T;
}
export async function put<T>(url: string, data: unknown): Promise<T> {
  return (await api.put(url, data)).data.data as T;
}
