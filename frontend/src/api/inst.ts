import { apiGet } from "./client";
import type { InstProfileDetail, InstProfileRow, InstSignal, ProfileOrderBy } from "./types";

export function fetchProfiles(opts: {
  orderBy?: ProfileOrderBy;
  minEpisodes?: number;
  holderType?: string;
  limit?: number;
} = {}): Promise<InstProfileRow[]> {
  const q = new URLSearchParams();
  if (opts.orderBy) q.set("order_by", opts.orderBy);
  if (opts.minEpisodes !== undefined) q.set("min_episodes", String(opts.minEpisodes));
  if (opts.holderType) q.set("holder_type", opts.holderType);
  if (opts.limit !== undefined) q.set("limit", String(opts.limit));
  const qs = q.toString();
  return apiGet<{ status: string; profiles: InstProfileRow[] }>(
    `/api/v3/inst/profiles${qs ? `?${qs}` : ""}`,
  ).then((r) => r.profiles);
}

export function fetchProfile(holder: string): Promise<InstProfileDetail> {
  return apiGet<{ status: string; profile: InstProfileDetail }>(
    `/api/v3/inst/profiles/${encodeURIComponent(holder)}`,
  ).then((r) => r.profile);
}

export function fetchSignals(opts: { days?: number; limit?: number } = {}): Promise<InstSignal[]> {
  const q = new URLSearchParams();
  if (opts.days !== undefined) q.set("days", String(opts.days));
  if (opts.limit !== undefined) q.set("limit", String(opts.limit));
  const qs = q.toString();
  return apiGet<{ status: string; signals: InstSignal[] }>(
    `/api/v3/inst/signals${qs ? `?${qs}` : ""}`,
  ).then((r) => r.signals);
}
