import { request } from "../lib/api";

const post = <T,>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) });

export interface Anomaly { timestamp: string; in_util: number; out_util: number; score: number }
export interface AnomalyResult {
  device: string; interface: string; points: number; anomalies: Anomaly[];
  series: { timestamps: string[]; in_util: number[]; out_util: number[] };
}
export interface AnomalyResponse { algorithm: string; interfaces: number; anomalous_interfaces: number; results: AnomalyResult[]; source: string; notes: string[] }

export interface Cluster { cluster: number; size: number; label: string; center: Record<string, number>; members: { device: string; interface: string }[] }
export interface ClusterResponse {
  k: number; silhouette: number | null; k_search: { k: number; silhouette: number }[]; clusters: Cluster[];
  points: { device: string; interface: string; cluster: number; mean_in: number; mean_out: number }[]; source: string;
}

export interface Feature { feature: string; importance: number }
export interface ClassModel { algorithm: string; accuracy: number; precision: number; recall: number; f1: number; roc_auc: number; threshold: number; top_features: Feature[] }
export interface ClassResponse { source: string; notes: string[]; rows: number; failure_rate: number; test_rows: number; best: string; models: ClassModel[] }

export interface RegModel { algorithm: string; mae: number; rmse: number; r2: number; top_features: Feature[]; sample: { actual: number; predicted: number }[] }
export interface RegResponse { task: string; source?: string; notes?: string[]; rows: number; best: string; models: RegModel[] }

export interface PolicyRow { devices: string; risk: string; wave_size: number; q_values: Record<string, number> }
export interface RLResponse { episodes: number; blast_weight: number; actions: number[]; policy: PolicyRow[]; learning_curve: { episode: number; mean_reward: number }[] }

export interface LogIncident { device: string; kind: "burst" | "critical" | "new_event" | "unusual_mix"; event: string; count: number; window_start: string; interfaces: string[]; explanation: string; score: number; samples: string[] }
export interface LogDevice { device: string; events: number; by_severity: Record<string, number>; top_events: [string, number][]; incidents: number; status: string }
export interface LogResponse {
  events: number; windows: number; window_minutes: number; source: string; devices: LogDevice[]; incidents: LogIncident[];
  timeline: { windows: string[]; devices: Record<string, number[]> };
  collection: Record<string, { platform: string; lines?: number; parsed?: number; unparsed?: number; template?: string; note?: string; error?: string }>;
  netbox: { device: string; kind?: string; journal_entry?: string; error?: string }[];
}

export interface RiskResponse { failure_probability: number; model: string; source: string; recommended_wave_size: number }

export const mlApi = {
  anomalies: (body: { algorithm: string; hours: number }) => post<AnomalyResponse>("/ml/anomalies", body),
  clusters: (body: { k: number | null; hours: number }) => post<ClusterResponse>("/ml/clusters", body),
  classification: (body: { algorithms?: string[] }) => post<ClassResponse>("/ml/classification", body),
  regression: (body: { target: string; algorithms?: string[] }) => post<RegResponse>("/ml/regression", body),
  reinforcement: (body: { episodes: number; blast_weight: number }) => post<RLResponse>("/ml/reinforcement", body),
  risk: (body: Record<string, unknown>) => post<RiskResponse>("/ml/risk", body),
  logs: (body: { hours: number; window_minutes: number; push_to_netbox: boolean }) => post<LogResponse>("/ml/log-anomalies", body),
};
