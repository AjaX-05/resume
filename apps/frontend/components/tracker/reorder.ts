import { arrayMove } from '@dnd-kit/sortable';
import {
  APPLICATION_STATUS_ORDER,
  type ApplicationColumns,
  type ApplicationStatus,
} from '@/lib/api/tracker';
import { cardSeniority } from '@/lib/scrape-meta';

const COLUMN_PREFIX = 'column:';

function parseColumnDroppable(
  overId: string
): { status: ApplicationStatus; lane?: string } | null {
  if (!overId.startsWith(COLUMN_PREFIX)) return null;
  const [status, lane] = overId.slice(COLUMN_PREFIX.length).split(':');
  if (!APPLICATION_STATUS_ORDER.includes(status as ApplicationStatus)) return null;
  return { status: status as ApplicationStatus, lane };
}

export interface MovePlan {
  /** The reordered columns with positions renumbered 0..n-1. */
  next: ApplicationColumns;
  /** The target column the card now belongs to. */
  status: ApplicationStatus;
  /** The card's index within the target column (the server-side `position`). */
  position: number;
}

function locate(
  columns: ApplicationColumns,
  cardId: string
): { status: ApplicationStatus; index: number } | null {
  for (const status of APPLICATION_STATUS_ORDER) {
    const index = columns[status].findIndex((a) => a.application_id === cardId);
    if (index >= 0) return { status, index };
  }
  return null;
}

/**
 * Pure drag-end resolution shared by the board and its tests.
 *
 * ``overId`` is either a card id or a column droppable id (``column:<status>``,
 * so empty columns are valid drop targets). Returns ``null`` for a no-op move.
 * Positions in every touched column are renumbered to a contiguous sequence so
 * the optimistic state matches what the server will persist.
 */
export function planMove(
  columns: ApplicationColumns,
  activeId: string,
  overId: string
): MovePlan | null {
  if (activeId === overId) return null;

  const source = locate(columns, activeId);
  if (!source) return null;

  const columnDrop = parseColumnDroppable(overId);
  const overIsColumn = columnDrop !== null;
  const targetStatus: ApplicationStatus = overIsColumn
    ? columnDrop.status
    : (locate(columns, overId)?.status ?? source.status);

  // JD is split into seniority lanes; dropping onto a different lane does not
  // change seniority (that comes from the title), so the move is a no-op.
  if (
    overIsColumn &&
    source.status === 'saved' &&
    targetStatus === 'saved' &&
    columnDrop.lane
  ) {
    const card = columns[source.status][source.index];
    if (cardSeniority(card.role, card.notes) !== columnDrop.lane) return null;
  }

  if (source.status === targetStatus) {
    const overIndex = overIsColumn
      ? columns[targetStatus].length - 1
      : locate(columns, overId)!.index;
    if (overIndex === source.index) return null;
    const reordered = arrayMove(columns[targetStatus], source.index, overIndex).map((a, i) => ({
      ...a,
      position: i,
    }));
    return {
      next: { ...columns, [targetStatus]: reordered },
      status: targetStatus,
      position: reordered.findIndex((a) => a.application_id === activeId),
    };
  }

  const sourceList = [...columns[source.status]];
  const [moved] = sourceList.splice(source.index, 1);
  const targetList = [...columns[targetStatus]];
  const insertAt = overIsColumn ? targetList.length : locate(columns, overId)!.index;
  targetList.splice(insertAt, 0, { ...moved, status: targetStatus });

  return {
    next: {
      ...columns,
      [source.status]: sourceList.map((a, i) => ({ ...a, position: i })),
      [targetStatus]: targetList.map((a, i) => ({ ...a, position: i })),
    },
    status: targetStatus,
    position: insertAt,
  };
}
