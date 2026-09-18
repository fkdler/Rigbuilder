import axios from "axios";
import { http } from "./http";

export interface PageResult<T> { total: number; items: T[] }
export interface UsageDay { day: string; total_tokens: number; calls: number; unknown_calls: number }
export interface TimingMetrics {
  samples: number; avg_response_seconds: number | null;
  avg_execution_seconds: number | null; avg_start_wait_seconds: number | null;
}
export interface SiteUsage {
  total_tokens: number; calls: number; unknown_calls: number;
  period_tokens: number; period_calls: number; period_unknown_calls: number;
  first_record_at: string | null; metering_since: string | null;
  start: string; end: string; timezone: string; note: string; by_day: UsageDay[];
  timing?: TimingMetrics & { note: string; by_day: (TimingMetrics & { day: string })[] };
}
export interface AdminUser {
  id: string; username: string; role: string; status: string;
  created_at: string; last_login_at: string | null;
}
export interface CatalogEntity {
  id: string; entity_key: string; entity_type: string; canonical_name: string;
  lifecycle_status: string; recommendable: boolean; release_date: string | null; updated_at: string;
}
export interface CatalogDetail {
  entity: CatalogEntity;
  sections: { table: string; total: number; rows: Record<string, unknown>[] }[];
}
export type EntityEdit = Pick<CatalogEntity, "canonical_name" | "lifecycle_status" | "recommendable" | "release_date" | "updated_at">;

export interface MaintenanceField {
  key: string; label: string; kind: "integer" | "number" | "text" | "boolean";
  unit: string; minimum: number; maximum: number; value: string | number | boolean | null; missing: boolean;
}
export interface MaintenanceState {
  revision: string; fields: MaintenanceField[]; aliases: string[];
  prices: { amount: string; currency: string; market_region: string; condition: string; observed_at: string; notes: string | null }[];
  changes: { id: string; action: string; created_at: string; before: Record<string, unknown>; after: Record<string, unknown> }[];
}
export interface Provenance { title: string; url: string; excerpt: string; reason: string; confirmed: boolean }
export const getMaintenance = async (id: string) => (await http.get<MaintenanceState>(`/api/admin/catalog/${id}/maintenance`)).data;
export const saveMaintenance = async (id: string, body: { revision: string; values: Record<string, string | boolean | null>; aliases?: string[]; provenance?: Provenance }) => (await http.patch<MaintenanceState>(`/api/admin/catalog/${id}/maintenance`, body)).data;
export const appendQuote = async (id: string, body: { revision: string; amount: string; currency: string; market_region: string; condition: string; observed_at: string; provenance: Provenance }) => (await http.post<MaintenanceState>(`/api/admin/catalog/${id}/quotes`, body)).data;

export const getUsage = async (start: string, end: string) => (await http.get<SiteUsage>("/api/admin/usage", { params: { start, end } })).data;
export const getUsers = async (q: string, offset: number) => (await http.get<PageResult<AdminUser>>("/api/admin/users", { params: { q, offset, limit: 25 } })).data;
export const deleteUser = async (user: AdminUser) => http.delete(`/api/admin/users/${user.id}`, { data: { username: user.username } });
export const getCatalog = async (kind: string, q: string, offset: number) => (await http.get<PageResult<CatalogEntity>>("/api/admin/catalog", { params: { kind, q, offset, limit: 25 } })).data;
export const getEntity = async (id: string) => (await http.get<CatalogDetail>(`/api/admin/catalog/${id}`)).data;
export const saveEntity = async (id: string, body: EntityEdit) => (await http.patch<CatalogEntity>(`/api/admin/catalog/${id}`, body)).data;
export function adminError(cause: unknown): string {
  if (axios.isAxiosError(cause)) {
    const detail = cause.response?.data?.detail;
    if (cause.response?.status === 404 && detail === "Not Found") {
      return "当前后端尚未加载管理员接口，请重启新版后端，并确认前端代理指向正确的服务。";
    }
    if (typeof detail === "string") return detail;
    if (typeof detail?.message === "string") return detail.message;
    if (cause.response?.status === 422) return "输入格式不正确，请检查后重试。";
  }
  return "请求失败，请检查服务连接后重试。";
}
export const number = (value: number) => value.toLocaleString("zh-CN");
export const dateTime = (value: string | null) => value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "—";
