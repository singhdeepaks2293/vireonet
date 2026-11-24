import { request } from "../lib/api";

export interface PushDevice { device: string; config_lines: number; rollback_lines: number; config_preview: string }
export interface PushPreview { valid: boolean; errors: string[]; warnings: string[]; devices: PushDevice[] }
export interface PushResult {
  change: { id: string; ticket_number: string; state: string; devices: string[]; change_type: string };
  created: boolean; workflow_started: boolean; workbook_id: string; warnings: string[]; skipped_attachments?: string[];
  automation?: "manual" | "full"; window?: { start: string; end: string };
}
export interface StateResult extends Partial<PushResult> { status: "ignored" | "started" | "exists"; ticket: string; state: string; reason?: string }
export type Automation = "manual" | "full";
export interface ItsmTicket { provider: string; ticket: { key: string; summary: string; status: string; requester: string }; attachments: { id: string; filename: string; size: number }[] }

const post = <T,>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) });

export const pushApi = {
  preview: (filename: string, content_b64: string) => post<PushPreview>("/config-push/preview", { filename, content_b64 }),
  submit: (body: { filename: string; content_b64: string; ticket_number: string; site: string; emergency: boolean; automation: Automation; planned_start: string; planned_end: string }) => post<PushResult>("/config-push", body),
  fromTicket: (body: { ticket_number: string; site: string; emergency: boolean; automation: Automation }) => post<PushResult>("/config-push/from-ticket", body),
  /** Same call the ITSM tool makes when a ticket changes state (demo use in fake mode). */
  stateEvent: (ticket_number: string) => post<StateResult>("/webhooks/itsm/state", { ticket_number }),
  ticket: (key: string) => request<ItsmTicket>(`/itsm/tickets/${encodeURIComponent(key)}`),
  health: () => request<{ backend: string | null; status: string; detail?: string }>("/itsm/health"),
};

/** File -> base64 (no data: prefix). */
export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",", 2)[1] ?? "");
    r.onerror = () => reject(new Error("could not read the file"));
    r.readAsDataURL(file);
  });
}
