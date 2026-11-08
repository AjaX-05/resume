import { apiFetch, apiPost } from './client';

export type ScraperSource = 'all' | 'jobright' | 'wellfound' | 'ycjobs';

export interface ScraperStatus {
  running: boolean;
  stopping?: boolean;
  source: string | null;
  last_run_at: string | null;
  last_jobs_found: number;
  last_error: string | null;
  last_log_tail?: string | null;
  lookback_minutes?: number;
}

export async function runScraper(
  source: ScraperSource = 'all',
  lookbackMinutes = 60
): Promise<{ status: string }> {
  const res = await apiPost('/scraper/run', { source, lookback_minutes: lookbackMinutes });
  if (!res.ok) throw new Error(`Scraper run failed (${res.status})`);
  return res.json();
}

export async function stopScraper(): Promise<{ status: string }> {
  const res = await apiPost('/scraper/stop', {});
  if (!res.ok) throw new Error(`Scraper stop failed (${res.status})`);
  return res.json();
}

export async function getScraperStatus(): Promise<ScraperStatus> {
  const res = await apiFetch('/scraper/status');
  if (!res.ok) throw new Error(`Scraper status failed (${res.status})`);
  return res.json();
}

export interface SyncResult {
  synced: number;
  skipped: number;
  errors?: number;
  message?: string;
}

export async function syncExistingDiscoveries(): Promise<SyncResult> {
  const res = await apiPost('/scraper/sync-existing', {});
  if (!res.ok) throw new Error(`Sync failed (${res.status})`);
  return res.json();
}
