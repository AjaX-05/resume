import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { KanbanBoard } from '@/components/tracker/kanban-board';
import {
  APPLICATION_STATUS_ORDER,
  listApplications,
  type Application,
  type ApplicationColumns,
} from '@/lib/api/tracker';
import { SENIORITY_ORDER } from '@/lib/scrape-meta';

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

vi.mock('@/lib/api/pipeline', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/pipeline')>('@/lib/api/pipeline');
  return {
    ...actual,
    detectAts: vi.fn(async (ids: string[]) =>
      Object.fromEntries(
        ids.map((id) => [id, { ats: 'greenhouse', supported: true, apply_url: 'https://example.com' }])
      )
    ),
    getPipelineConfig: vi.fn(async () => ({
      master_resume_id: 'master-1',
      tailor_prompt_id: 'full',
    })),
  };
});

function app(id: string, role: string, seniority: string, position: number): Application {
  return {
    application_id: id,
    job_id: `job-${id}`,
    resume_id: `res-${id}`,
    master_resume_id: null,
    status: 'saved',
    company: 'Acme',
    role,
    applied_at: null,
    notes: `Seniority: ${seniority}`,
    position,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };
}

function columnsFixture(): ApplicationColumns {
  const columns = APPLICATION_STATUS_ORDER.reduce((acc, status) => {
    acc[status] = [];
    return acc;
  }, {} as ApplicationColumns);
  columns.saved = [
    app('senior-a', 'Senior Engineer', 'Senior', 0),
    app('senior-b', 'Senior SWE', 'Senior', 1),
    app('entry-a', 'Junior Developer', 'Entry', 2),
  ];
  return columns;
}

describe('KanbanBoard JD seniority lanes', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(listApplications).mockResolvedValue({ columns: columnsFixture() });
  });

  it('splits JD into one column per seniority with a select-all checkbox', async () => {
    render(<KanbanBoard />);

    for (const level of SENIORITY_ORDER) {
      expect(await screen.findByRole('heading', { name: `tracker.columns.saved-${level}` })).toBeTruthy();
    }

    const seniorHeading = screen.getByRole('heading', { name: 'tracker.columns.saved-Senior' });
    const seniorHeader = seniorHeading.closest('div')?.parentElement;
    expect(seniorHeader).toBeTruthy();
    fireEvent.click(within(seniorHeader!).getByRole('checkbox'));

    await waitFor(() => {
      expect(screen.getByText('tracker.bulk.selected')).toBeTruthy();
    });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Tailor 2/ })).toBeTruthy();
    });

    const entryHeading = screen.getByRole('heading', { name: 'tracker.columns.saved-Entry' });
    const entryHeader = entryHeading.closest('div')?.parentElement;
    const entryCheckbox = within(entryHeader!).getByRole('checkbox');
    expect(entryCheckbox).not.toBeChecked();
  });
});
