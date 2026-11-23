/**
 * Generated from the FastAPI OpenAPI schema. Do not edit by hand.
 * Regenerate: npm run generate:api
 */
export interface ChangeResponse {
  id: string;
  ticket_number: string;
  change_type: string;
  state: string;
  requester: string;
  devices: string[];
  created_at?: string | null;
}
export interface PaginatedChanges {
  items: ChangeResponse[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}
export interface TransitionResponse {
  from_state: string;
  to_state: string;
  actor: string;
  reason: string;
  evidence: Record<string, unknown>;
  created_at?: string | null;
}
export interface DiffLineResponse {
  old_line?: number | null;
  new_line?: number | null;
  type: string;
  text: string;
}
export interface BatfishReachabilitySummary {
  lost: number;
  gained: number;
  changed: number;
  total: number;
  lost_sample: Record<string, unknown>[];
  gained_sample: Record<string, unknown>[];
}
export interface BatfishAclImpact {
  file: string;
  line: string;
  flows_lost: number;
  flows_gained: number;
  attribution: string;
}
export interface BatfishDifferential {
  differential: boolean;
  note?: string;
  reachability?: BatfishReachabilitySummary;
  changed_files?: string[];
  acl_impact?: BatfishAclImpact[];
}
export interface BatfishGateResult {
  passed: boolean;
  blocking?: boolean;
  blocks?: boolean;
  failures: string[];
  evidence?: Record<string, unknown>;
  differential?: BatfishDifferential;
  files?: string[];
  warning?: string | null;
}
export interface ChangeDetailResponse {
  change: ChangeResponse;
  intent: Record<string, unknown>;
  diff: DiffLineResponse[];
  checks: { name: string; status: string; message: string }[];
  batfish: BatfishGateResult;
  awx_jobs: { job_id?: number | null; status?: string; to_state?: string }[];
  transitions: TransitionResponse[];
}
export interface CreateChangeRequest {
  intent?: Record<string, unknown> | null;
  ticket_number?: string | null;
  sys_id?: string | null;
  emergency?: boolean;
}
export interface ApprovalSignal { approved: boolean; reason?: string }
export interface CiSignal { passed: boolean; reason?: string; pipeline_url?: string }
export interface MergeSignal { merged?: boolean }
export interface AdapterHealth {
  name: string;
  status: string;
  latency_ms?: number | null;
  last_success?: string | null;
  capabilities: string[];
  detail: string;
}
export interface ZtpDeviceResponse {
  serial: string;
  hostname: string;
  model: string;
  platform: string;
  site: string;
  stage: string;
  ip_address?: string | null;
  version?: string | null;
  callback_at?: string | null;
  ticket_number: string;
  netbox_status: string;
  dhcp_reservation: string;
}
export interface PaginatedZtpDevices {
  items: ZtpDeviceResponse[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}
export interface LockResponse {
  device: string;
  ticket_number: string;
  holder: string;
  acquired_at?: string | null;
  expires_at?: string | null;
}
export interface ChangeWindowResponse {
  days: string[];
  start: string;
  end: string;
  timezone: string;
  emergency_bypasses_window: boolean;
  currently_in_window: boolean;
  next_window_start?: string | null;
}
export interface ComponentHealth {
  name: string;
  status: string;
  detail: string;
}
export interface SystemHealthResponse {
  status: string;
  components: ComponentHealth[];
}
export interface AdapterSettingResponse {
  name: string;
  enabled: boolean;
  mode: string;
  endpoint: string;
  timeout_seconds?: number | null;
  retry_max_attempts?: number | null;
  rate_limit_per_second?: number | null;
  capabilities: string[];
}
export interface IamUser {
  id: string;
  username: string;
  display_name: string;
  role: string;
  is_active: boolean;
  auth_provider: string;
  created_at?: string | null;
}
export interface IamRolesResponse {
  roles: Record<string, string[]>;
  permissions: string[];
}
export interface LoginResponse {
  access_token: string;
  token_type: string;
  user_id: string;
  username: string;
  role: string;
}

export interface GiteaSetupJob {
  id: string;
  status: string;
  steps: { step: string; status: string; detail: string }[];
  error?: string;
}
export interface ConfigRepoResponse {
  hostname: string;
  repo: string;
  url?: string;
}
export interface ConfigVersionResponse {
  device: string;
  version: number;
  content_hash: string;
  ticket_number: string | null;
  source: string;
  created_at: string | null;
  lines: number;
}
