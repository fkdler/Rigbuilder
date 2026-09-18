import { isAxiosError } from "axios";

import { http } from "./http";

export interface AuthUser {
  id: string;
  username: string;
  role: string;
  created_at: string;
  last_login_at: string | null;
}

export interface SessionResponse {
  token: string;
  expires_at: string;
  user: AuthUser;
}

export async function registerAccount(username: string, password: string): Promise<SessionResponse> {
  return (await http.post<SessionResponse>("/api/auth/register", { username, password })).data;
}

export async function loginAccount(username: string, password: string): Promise<SessionResponse> {
  return (await http.post<SessionResponse>("/api/auth/login", { username, password })).data;
}

export async function logoutAccount(): Promise<void> {
  await http.post("/api/auth/logout");
}

export async function fetchMe(): Promise<AuthUser> {
  return (await http.get<AuthUser>("/api/auth/me")).data;
}

export async function updateUsername(username: string): Promise<AuthUser> {
  return (await http.patch<AuthUser>("/api/auth/me", { username })).data;
}

export async function updatePassword(currentPassword: string, newPassword: string): Promise<void> {
  await http.post("/api/auth/password", { current_password: currentPassword, new_password: newPassword });
}

/**
 * Turn any failure from the account endpoints into one sentence for the user.
 *
 * The server already returns a user-facing `detail.message`; this only adds a
 * fallback for transport failures, and deliberately never surfaces a raw status
 * code or stack -- Plan_V4.5 §11.3 U3 asks for errors a person can act on, without
 * leaking authentication internals.
 */
export function authErrorMessage(cause: unknown): string {
  if (isAxiosError(cause)) {
    const detail = cause.response?.data?.detail;
    if (typeof detail === "string" && detail) return detail;
    if (detail && typeof detail === "object" && typeof detail.message === "string") return detail.message;
    if (cause.code === "ECONNABORTED") return "请求超时，请检查后端服务后重试。";
    if (!cause.response) return "无法连接后端服务，请确认服务已启动。";
    if (cause.response.status === 429) return "尝试过于频繁，请稍后再试。";
    return `请求失败（${cause.response.status}）。`;
  }
  return cause instanceof Error && cause.message ? cause.message : "操作失败，请重试。";
}
