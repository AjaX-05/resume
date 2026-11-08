import { apiFetch, apiPost, apiPut } from './client';

export type ApplyStatus =
  | 'pending'
  | 'filling'
  | 'needs_review'
  | 'submitting'
  | 'submitted'
  | 'failed'
  | 'cancelled';

export interface ApplySession {
  application_id: string;
  apply_url: string;
  status: ApplyStatus;
  has_screenshot: boolean;
  filled_fields: Record<string, string>;
  error: string | null;
  started_at: string;
  completed_at: string | null;
}

export interface ApplyScreenshot {
  application_id: string;
  status: ApplyStatus;
  screenshot_b64: string | null;
}

export async function startApply(application_id: string, resume_id: string): Promise<ApplySession> {
  const res = await apiPost('/applier/start', { application_id, resume_id });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      (body as { detail?: string }).detail ?? `Failed to start apply (${res.status})`
    );
  }
  return res.json();
}

export async function getApplyStatus(application_id: string): Promise<ApplySession | null> {
  const res = await apiFetch(`/applier/status/${encodeURIComponent(application_id)}`);
  if (res.status === 404) return null; // session doesn't exist (never started or cleared)
  if (!res.ok) throw new Error(`Status check failed (${res.status})`);
  return res.json();
}

export async function getApplyScreenshot(application_id: string): Promise<ApplyScreenshot> {
  const res = await apiFetch(`/applier/screenshot/${encodeURIComponent(application_id)}`);
  return res.json();
}

export async function approveApply(application_id: string): Promise<ApplySession> {
  const res = await apiPost(`/applier/approve/${encodeURIComponent(application_id)}`, {});
  return res.json();
}

export async function rejectApply(application_id: string): Promise<ApplySession> {
  const res = await apiPost(`/applier/reject/${encodeURIComponent(application_id)}`, {});
  return res.json();
}

export async function getVault(): Promise<Record<string, unknown>> {
  const res = await apiFetch('/applier/vault');
  return res.json();
}

export async function updateVault(
  vault: Record<string, unknown>
): Promise<Record<string, unknown>> {
  const res = await apiPut('/applier/vault', vault);
  return res.json();
}
