import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ManageColumnsDialog, columnLabel } from '@/components/tracker/manage-columns-dialog';
import { KanbanBoard } from '@/components/tracker/kanban-board';
import {
  APPLICATION_STATUS_ORDER,
  listApplications,
  type Application,
  type ApplicationColumns,
} from '@/lib/api/tracker';
import {
  BOARD_COLUMN_ORDER,
  TRACKER_HIDDEN_STATUSES_KEY,
  type BoardColumnId,
} from '@/lib/utils/tracker-column-visibility';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}));

vi.mock('@/lib/i18n', () => ({
  useTranslations: () => ({
    t: (key: string) => key,
  }),
}));

vi.mock('@/lib/api/tracker', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/tracker')>('@/lib/api/tracker');
  return { ...actual, listApplications: vi.fn() };
});

const boardDialog = vi.hoisted(() => ({
  onToggle: null as ((columnId: string) => void) | null,
}));

vi.mock('@/components/tracker/manage-columns-dialog', async () => {
  const actual = await vi.importActual<typeof import('@/components/tracker/manage-columns-dialog')>(
    '@/components/tracker/manage-columns-dialog'
  );
  return {
    columnLabel: actual.columnLabel,
    ManageColumnsDialog: (props: Parameters<typeof actual.ManageColumnsDialog>[0]) => {
      boardDialog.onToggle = props.onToggle as (columnId: string) => void;
      return React.createElement(actual.ManageColumnsDialog, props);
    },
  };
});

const identityT = (key: string) => key;
const LAST_JD: BoardColumnId = 'saved:Mid';
const ALL_BUT_LAST_JD = BOARD_COLUMN_ORDER.filter((id) => id !== LAST_JD);

function switchFor(columnId: BoardColumnId): HTMLElement {
  const label = columnLabel(columnId, identityT).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return screen.getByRole('switch', {
    name: new RegExp(`^${label}\\s*tracker\\.manageDialog\\.(visible|hidden)$`),
  });
}

function rowFor(columnId: BoardColumnId): HTMLElement {
  const row = switchFor(columnId).closest('div');
  if (!row) throw new Error(`no row rendered for ${columnId}`);
  return row;
}

function visibleLaneKeys(): string[] {
  return Array.from(document.querySelectorAll('[data-column]')).map(
    (el) => el.getAttribute('data-column') ?? ''
  );
}

function visibleColumns(): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const key of visibleLaneKeys()) {
    const status = key.split(':')[0];
    if (!status || seen.has(status)) continue;
    seen.add(status);
    out.push(status);
  }
  return out;
}

describe('ManageColumnsDialog', () => {
  it('renders one switch per JD seniority lane and every later stage', () => {
    render(
      <ManageColumnsDialog
        open
        onOpenChange={vi.fn()}
        hiddenColumns={new Set(['interview'])}
        onToggle={vi.fn()}
      />
    );

    expect(screen.getAllByRole('switch')).toHaveLength(BOARD_COLUMN_ORDER.length);
    expect(switchFor('saved:Entry')).toBeInTheDocument();
    expect(switchFor('saved:Architect')).toBeInTheDocument();
  });

  it('reports toggles with the column id, including JD-Senior', () => {
    const onToggle = vi.fn();
    render(
      <ManageColumnsDialog
        open
        onOpenChange={vi.fn()}
        hiddenColumns={new Set()}
        onToggle={onToggle}
      />
    );

    fireEvent.click(switchFor('saved:Senior'));
    expect(onToggle).toHaveBeenCalledWith('saved:Senior');

    fireEvent.click(switchFor('interview'));
    expect(onToggle).toHaveBeenCalledWith('interview');
  });

  it('describes each stage by its state, not by an action', () => {
    render(
      <ManageColumnsDialog
        open
        onOpenChange={vi.fn()}
        hiddenColumns={new Set(['interview'])}
        onToggle={vi.fn()}
      />
    );

    expect(
      within(rowFor('interview')).getByText('tracker.manageDialog.hidden')
    ).toBeInTheDocument();
    expect(within(rowFor('applied')).getByText('tracker.manageDialog.visible')).toBeInTheDocument();
    expect(screen.getAllByText('tracker.manageDialog.hidden')).toHaveLength(1);
    expect(screen.getAllByText('tracker.manageDialog.visible')).toHaveLength(
      BOARD_COLUMN_ORDER.length - 1
    );
  });

  it('locks the last visible JD lane and explains why', () => {
    const onToggle = vi.fn();
    render(
      <ManageColumnsDialog
        open
        onOpenChange={vi.fn()}
        hiddenColumns={new Set(ALL_BUT_LAST_JD)}
        onToggle={onToggle}
      />
    );

    const lastVisible = switchFor(LAST_JD);
    expect(lastVisible).toBeDisabled();
    expect(screen.getAllByText('tracker.manageDialog.lastVisibleHint')).toHaveLength(1);

    fireEvent.click(lastVisible);
    expect(onToggle).not.toHaveBeenCalled();

    const hiddenSwitch = switchFor('applied');
    expect(hiddenSwitch).not.toBeDisabled();
    fireEvent.click(hiddenSwitch);
    expect(onToggle).toHaveBeenCalledWith('applied');
  });
});

describe('KanbanBoard column visibility', () => {
  const application: Application = {
    application_id: 'app-1',
    job_id: 'job-1',
    resume_id: 'res-1',
    master_resume_id: null,
    status: 'saved',
    company: 'ACME',
    role: 'Engineer',
    applied_at: null,
    notes: 'Seniority: Mid',
    position: 0,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };

  function columnsFixture(): ApplicationColumns {
    const columns = APPLICATION_STATUS_ORDER.reduce((acc, status) => {
      acc[status] = [];
      return acc;
    }, {} as ApplicationColumns);
    columns.saved = [application];
    return columns;
  }

  async function renderBoard() {
    render(<KanbanBoard />);
    await waitFor(() => expect(visibleLaneKeys().length).toBeGreaterThan(0));
  }

  beforeEach(() => {
    localStorage.clear();
    boardDialog.onToggle = null;
    vi.mocked(listApplications).mockResolvedValue({ columns: columnsFixture() });
  });

  it('does not write the stored selection back on mount', async () => {
    const stored = JSON.stringify(['rejected', 'interview']);
    localStorage.setItem(TRACKER_HIDDEN_STATUSES_KEY, stored);

    await renderBoard();

    expect(localStorage.getItem(TRACKER_HIDDEN_STATUSES_KEY)).toBe(stored);
    expect(visibleColumns()).toEqual(
      APPLICATION_STATUS_ORDER.filter((s) => s !== 'rejected' && s !== 'interview')
    );
  });

  it('hides a single JD seniority lane without hiding the others', async () => {
    await renderBoard();

    fireEvent.click(screen.getByRole('button', { name: 'tracker.manage' }));
    fireEvent.click(switchFor('saved:Mid'));

    expect(JSON.parse(localStorage.getItem(TRACKER_HIDDEN_STATUSES_KEY) ?? '[]')).toEqual([
      'saved:Mid',
    ]);
    expect(visibleLaneKeys()).not.toContain('saved:Mid');
    expect(visibleLaneKeys()).toContain('saved:Senior');
    expect(visibleLaneKeys()).toContain('applied');
  });

  it('persists a later stage the user actually hides', async () => {
    await renderBoard();

    fireEvent.click(screen.getByRole('button', { name: 'tracker.manage' }));
    fireEvent.click(switchFor('applied'));

    expect(localStorage.getItem(TRACKER_HIDDEN_STATUSES_KEY)).toBe(JSON.stringify(['applied']));
    expect(visibleColumns()).not.toContain('applied');

    fireEvent.click(switchFor('applied'));
    expect(localStorage.getItem(TRACKER_HIDDEN_STATUSES_KEY)).toBe(JSON.stringify([]));
    expect(visibleColumns()).toContain('applied');
  });

  it('refuses to hide the last visible lane even when the dialog is bypassed', async () => {
    const stored = JSON.stringify(ALL_BUT_LAST_JD);
    localStorage.setItem(TRACKER_HIDDEN_STATUSES_KEY, stored);

    await renderBoard();
    expect(visibleLaneKeys()).toEqual(['saved:Mid']);

    const toggle = boardDialog.onToggle;
    expect(toggle).toBeTypeOf('function');
    act(() => toggle?.(LAST_JD));

    expect(visibleLaneKeys()).toEqual(['saved:Mid']);
    expect(localStorage.getItem(TRACKER_HIDDEN_STATUSES_KEY)).toBe(stored);
  });
});
