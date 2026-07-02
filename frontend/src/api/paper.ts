import { apiDelete, apiGet, apiPost } from "./client";
import type {
  AddPositionReq,
  AddPositionResp,
  ClosePositionResp,
  MarkResp,
  PaperKpi,
  PaperNavPoint,
  PaperPosition,
} from "./types";

export interface PortfolioResp {
  positions: PaperPosition[];
  kpi: PaperKpi;
}

export function fetchPortfolio(): Promise<PortfolioResp> {
  return apiGet<{ status: string } & PortfolioResp>("/api/v3/paper/portfolio").then((r) => ({
    positions: r.positions,
    kpi: r.kpi,
  }));
}

export function fetchNav(): Promise<PaperNavPoint[]> {
  return apiGet<{ status: string; nav: PaperNavPoint[] }>("/api/v3/paper/nav").then((r) => r.nav);
}

export function addPosition(req: AddPositionReq): Promise<AddPositionResp> {
  return apiPost<{ status: string; data: AddPositionResp }>("/api/v3/paper/positions", req).then(
    (r) => r.data,
  );
}

export function closePosition(positionId: string): Promise<ClosePositionResp> {
  return apiDelete<{ status: string; data: ClosePositionResp }>(
    `/api/v3/paper/positions/${encodeURIComponent(positionId)}`,
  ).then((r) => r.data);
}

export function markToMarket(): Promise<MarkResp> {
  return apiPost<{ status: string; data: MarkResp }>("/api/v3/paper/mark").then((r) => r.data);
}
