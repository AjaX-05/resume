import { APPLICATION_STATUS_ORDER, type ApplicationStatus } from '@/lib/api/tracker';
import { SENIORITY_ORDER, type Seniority } from '@/lib/scrape-meta';
import { safeStorage } from '@/lib/utils/resume-draft-storage';

export const TRACKER_HIDDEN_STATUSES_KEY = 'tracker_hidden_statuses';

export type BoardColumnId =
  | `saved:${Seniority}`
  | Exclude<ApplicationStatus, 'saved'>;

export const BOARD_COLUMN_ORDER: BoardColumnId[] = [
  ...SENIORITY_ORDER.map((level) => `saved:${level}` as const),
  ...(APPLICATION_STATUS_ORDER.filter(
    (status): status is Exclude<ApplicationStatus, 'saved'> => status !== 'saved'
  )),
];

const VALID_COLUMNS = new Set<string>(BOARD_COLUMN_ORDER);

export function isBoardColumnId(value: string): value is BoardColumnId {
  return VALID_COLUMNS.has(value);
}

function expandStoredValue(value: string, into: Set<BoardColumnId>): void {
  if (value === 'saved') {
    for (const level of SENIORITY_ORDER) into.add(`saved:${level}`);
    return;
  }
  if (isBoardColumnId(value)) into.add(value);
}

export function readHiddenColumns(): Set<BoardColumnId> {
  if (typeof window === 'undefined') return new Set();

  const raw = safeStorage.get(TRACKER_HIDDEN_STATUSES_KEY);
  if (!raw) return new Set();

  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();

    const hidden = new Set<BoardColumnId>();
    for (const value of parsed) {
      if (typeof value === 'string') expandStoredValue(value, hidden);
    }
    return hidden;
  } catch {
    return new Set();
  }
}

/** @deprecated Use readHiddenColumns — kept for tests that still talk in statuses. */
export function readHiddenStatuses(): Set<ApplicationStatus> {
  const hidden = readHiddenColumns();
  const statuses = new Set<ApplicationStatus>();
  for (const id of hidden) {
    if (id.startsWith('saved:')) statuses.add('saved');
    else statuses.add(id as Exclude<ApplicationStatus, 'saved'>);
  }
  // Only mark JD hidden when every seniority lane is hidden.
  if (SENIORITY_ORDER.some((level) => !hidden.has(`saved:${level}`))) {
    statuses.delete('saved');
  }
  return statuses;
}

export function writeHiddenColumns(hidden: Set<BoardColumnId>): boolean {
  const ordered = BOARD_COLUMN_ORDER.filter((id) => hidden.has(id));
  return safeStorage.set(TRACKER_HIDDEN_STATUSES_KEY, JSON.stringify(ordered));
}

/** @deprecated Use writeHiddenColumns. */
export function writeHiddenStatuses(hidden: Set<ApplicationStatus>): boolean {
  const columns = new Set<BoardColumnId>();
  for (const status of hidden) {
    if (status === 'saved') {
      for (const level of SENIORITY_ORDER) columns.add(`saved:${level}`);
    } else {
      columns.add(status);
    }
  }
  return writeHiddenColumns(columns);
}

export function isLastVisibleColumn(
  hidden: Set<BoardColumnId>,
  columnId: BoardColumnId
): boolean {
  if (hidden.has(columnId)) return false;
  return BOARD_COLUMN_ORDER.every((candidate) => candidate === columnId || hidden.has(candidate));
}

/** @deprecated Use isLastVisibleColumn. */
export function isLastVisibleStatus(
  hidden: Set<ApplicationStatus>,
  status: ApplicationStatus
): boolean {
  if (hidden.has(status)) return false;
  return APPLICATION_STATUS_ORDER.every(
    (candidate) => candidate === status || hidden.has(candidate)
  );
}

export function toggleHiddenColumn(
  hidden: Set<BoardColumnId>,
  columnId: BoardColumnId
): Set<BoardColumnId> {
  const next = new Set(hidden);
  if (next.delete(columnId)) return next;
  if (isLastVisibleColumn(hidden, columnId)) return hidden;
  next.add(columnId);
  return next;
}

/** @deprecated Use toggleHiddenColumn. */
export function toggleHiddenStatus(
  hidden: Set<ApplicationStatus>,
  status: ApplicationStatus
): Set<ApplicationStatus> {
  const next = new Set(hidden);
  if (next.delete(status)) return next;
  if (isLastVisibleStatus(hidden, status)) return hidden;
  next.add(status);
  return next;
}
