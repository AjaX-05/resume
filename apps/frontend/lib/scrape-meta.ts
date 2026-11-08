export const LOOKBACK_OPTIONS = [
  { minutes: 30, label: 'Last 30 min' },
  { minutes: 60, label: 'Last 1 hr' },
  { minutes: 120, label: 'Last 2 hr' },
  { minutes: 180, label: 'Last 3 hr' },
  { minutes: 240, label: 'Last 4 hr' },
  { minutes: 360, label: 'Last 6 hr' },
  { minutes: 720, label: 'Last 12 hr' },
  { minutes: 1440, label: 'Last 24 hr' },
] as const;

export type Seniority = 'Entry' | 'Mid' | 'Senior' | 'Staff' | 'Lead' | 'Principal' | 'Architect';

export const SENIORITY_ORDER: readonly Seniority[] = [
  'Entry',
  'Mid',
  'Senior',
  'Staff',
  'Lead',
  'Principal',
  'Architect',
];

export interface ScrapeMeta {
  postedAt: string | null;
  postedLabel: string | null;
  seniority: string | null;
  scrapeBatch: string | null;
  lookback: string | null;
  source: string | null;
}

const LINE = (name: string) => new RegExp(`^${name}:\\s*(.+)$`, 'im');

export function cardSeniority(
  role: string | null | undefined,
  notes: string | null | undefined
): Seniority {
  const listed = parseScrapeMeta(notes).seniority;
  if (listed && (SENIORITY_ORDER as readonly string[]).includes(listed)) {
    return listed as Seniority;
  }
  return classifySeniority(role || '');
}

export function classifySeniority(title: string): Seniority {
  const t = title.toLowerCase();
  if (/\b(intern|internship|co-?op|new grad|university|entry[ -]?level|junior|jr\.?)\b/.test(t)) {
    return 'Entry';
  }
  if (/\barchitect\b/.test(t)) return 'Architect';
  if (/\b(principal|distinguished|fellow)\b/.test(t)) return 'Principal';
  if (/\bstaff\b/.test(t)) return 'Staff';
  if (/\b(director|head of|engineering manager|\bmanager\b|lead)\b/.test(t)) return 'Lead';
  if (/\b(senior|sr\.?)\b/.test(t)) return 'Senior';
  return 'Mid';
}

export function parseScrapeMeta(notes: string | null | undefined): ScrapeMeta {
  const text = notes || '';
  const pick = (name: string) => {
    const m = LINE(name).exec(text);
    return m ? m[1].trim() : null;
  };
  return {
    postedAt: pick('Posted'),
    postedLabel: pick('Posted-label'),
    seniority: pick('Seniority'),
    scrapeBatch: pick('Scrape-batch'),
    lookback: pick('Lookback'),
    source: pick('Source')?.split('|')[0]?.trim() || null,
  };
}

export function latestScrapeBatch(notesList: Array<string | null | undefined>): string | null {
  let latest: string | null = null;
  for (const notes of notesList) {
    const batch = parseScrapeMeta(notes).scrapeBatch;
    if (batch && (!latest || batch > latest)) latest = batch;
  }
  return latest;
}

export function formatPostedAgo(iso: string | null | undefined, fallback?: string | null): string {
  if (!iso) return fallback || 'Posted time unknown';
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return fallback || 'Posted time unknown';
  const mins = Math.max(0, Math.round((Date.now() - then.getTime()) / 60_000));
  if (mins < 1) return 'Posted just now';
  if (mins < 60) return `Posted ${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `Posted ${hours}h ago`;
  const days = Math.round(hours / 24);
  return `Posted ${days}d ago`;
}

export function postedWithinMinutes(iso: string | null | undefined, minutes: number): boolean {
  if (!iso) return true;
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return true;
  return Date.now() - then.getTime() <= minutes * 60_000;
}

export const SENIORITY_CLASS: Record<string, string> = {
  Entry: 'border-steel text-steel bg-paper',
  Mid: 'border-primary text-primary bg-info-tint',
  Senior: 'border-success text-success bg-success-tint',
  Staff: 'border-ink text-ink bg-canvas',
  Lead: 'border-warning-text text-warning-text bg-warning-tint',
  Principal: 'border-ink text-ink bg-warning-tint',
  Architect: 'border-ink text-ink bg-warning-tint',
};
