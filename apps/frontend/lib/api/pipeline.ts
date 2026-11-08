import { apiFetch, apiPost, apiPut } from './client';

export type PipelineItemStatus =
  | 'pending'
  | 'queued'
  | 'tailoring'
  | 'tailored'
  | 'applying'
  | 'needs_review'
  | 'applied'
  | 'failed'
  | 'skipped';

export interface PipelineItem {
  application_id: string;
  company: string;
  role: string;
  ats: string;
  status: PipelineItemStatus;
  queue_item_id: string | null;
  tailored_resume_id: string | null;
  error: string | null;
}

export interface PipelineTask {
  task_id: string;
  kind: 'tailor' | 'apply';
  total: number;
  counts: Record<string, number>;
  created_at: string;
  completed_at: string | null;
  items: PipelineItem[];
}

export interface PipelineConfig {
  auto_tailor: boolean;
  auto_apply: boolean;
  approval_gate: boolean;
  master_resume_id: string;
  tailor_prompt_id: string;
}

export interface AtsInfo {
  ats: string;
  supported: boolean;
  apply_url: string;
}

export type AtsMap = Record<string, AtsInfo>;

export async function detectAts(application_ids: string[]): Promise<AtsMap> {
  const res = await apiPost('/pipeline/ats-detect', { application_ids });
  if (!res.ok) throw new Error(`ATS detect failed (${res.status})`);
  return res.json();
}

export async function bulkTailor(
  application_ids: string[],
  master_resume_id: string,
  prompt_id = 'full',
  rate_limit_seconds = 5.0
): Promise<PipelineTask> {
  const res = await apiPost('/pipeline/bulk-tailor', {
    application_ids,
    master_resume_id,
    prompt_id,
    rate_limit_seconds,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail ?? `Bulk tailor failed (${res.status})`);
  }
  return res.json();
}

export async function bulkApply(application_ids: string[]): Promise<PipelineTask> {
  const res = await apiPost('/pipeline/bulk-apply', { application_ids });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail ?? `Bulk apply failed (${res.status})`);
  }
  return res.json();
}

export async function getPipelineTask(task_id: string): Promise<PipelineTask | null> {
  const res = await apiFetch(`/pipeline/status/${encodeURIComponent(task_id)}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Status failed (${res.status})`);
  return res.json();
}

export async function listPipelineTasks(): Promise<PipelineTask[]> {
  const res = await apiFetch('/pipeline/tasks');
  if (!res.ok) throw new Error(`Tasks failed (${res.status})`);
  return res.json();
}

export async function getPipelineConfig(): Promise<PipelineConfig> {
  const res = await apiFetch('/pipeline/config');
  if (!res.ok) throw new Error(`Config failed (${res.status})`);
  return res.json();
}

export async function updatePipelineConfig(cfg: PipelineConfig): Promise<PipelineConfig> {
  const res = await apiPut('/pipeline/config', cfg);
  if (!res.ok) throw new Error(`Config update failed (${res.status})`);
  return res.json();
}
