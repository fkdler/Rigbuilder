import axios from "axios";

import { http } from "./http";

export interface HardwareDailyArticle {
  title: string;
  content_id: string;
  content_text: string;
  url: string;
  comment_count: number;
  vote_up_count: number;
  author_name: string;
  author_profile_url: string;
  author_badge_text: string | null;
  edit_time: string;
  authority_level: string | null;
  thumbnail_url: string | null;
  content_is_excerpt: boolean;
}

export interface HardwareDailyResponse {
  articles: HardwareDailyArticle[];
  cache: { status: "fresh" | "stale"; fetched_at: string; expires_at: string };
}

export async function getHardwareDailies(): Promise<HardwareDailyResponse> {
  return (await http.get<HardwareDailyResponse>("/api/hardware-daily/latest")).data;
}

export function hardwareDailyError(cause: unknown): string {
  if (axios.isAxiosError(cause)) {
    const detail = cause.response?.data?.detail;
    if (typeof detail?.message === "string") return detail.message;
    if (typeof detail === "string") return detail;
  }
  return "暂时无法读取硬件日报，请稍后重新打开此模块。";
}
