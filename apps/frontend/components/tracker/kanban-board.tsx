'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import {
  DndContext,
  DragEndEvent,
  PointerSensor,
  KeyboardSensor,
  closestCorners,
  useSensor,
  useSensors,
} from '@dnd-kit/core';
import { sortableKeyboardCoordinates } from '@dnd-kit/sortable';
import { useReducedMotion } from 'motion/react';
import { ArrowLeft, Plus, Gear, SpinnerGap, CaretLeft, CaretRight } from '@phosphor-icons/react';
import { Button, buttonClass } from '@/components/ui/button';
import { Alert } from '@/components/ui/alert';
import { EmptyState } from '@/components/ui/empty-state';
import { useTranslations } from '@/lib/i18n';
import {
  listApplications,
  updateApplication,
  bulkUpdateStatus,
  bulkDeleteApplications,
  APPLICATION_STATUS_ORDER,
  type Application,
  type ApplicationColumns,
  type ApplicationStatus,
} from '@/lib/api/tracker';
import { KanbanColumn } from './kanban-column';
import { BulkActionBar } from './bulk-action-bar';
import { CardDetailModal } from './card-detail-modal';
import { ManualAddApplicationDialog } from './manual-add-application-dialog';
import { PipelineProgressModal } from './pipeline-progress-modal';
import { planMove } from './reorder';
import { ManageColumnsDialog } from './manage-columns-dialog';
import {
  readHiddenColumns,
  toggleHiddenColumn,
  writeHiddenColumns,
  type BoardColumnId,
} from '@/lib/utils/tracker-column-visibility';
import { useAtsMap } from '@/hooks/use-ats-map';
import { bulkTailor, bulkApply, getPipelineConfig, type PipelineTask } from '@/lib/api/pipeline';
import { fetchResumeList } from '@/lib/api/resume';
import { cardFingerprint, pickUniqueTailorIds } from '@/lib/card-fingerprint';
import {
  cardSeniority,
  latestScrapeBatch,
  parseScrapeMeta,
  postedWithinMinutes,
  SENIORITY_ORDER,
  type Seniority,
} from '@/lib/scrape-meta';

function emptyColumns(): ApplicationColumns {
  return APPLICATION_STATUS_ORDER.reduce((acc, status) => {
    acc[status] = [];
    return acc;
  }, {} as ApplicationColumns);
}

type BoardLane = {
  key: string;
  status: ApplicationStatus;
  seniority?: Seniority;
  title: string;
  applications: Application[];
  droppableId: string;
};

interface KanbanBoardProps {
  reloadToken?: number;
  postedFilterMinutes?: number | null;
  /** When Auto-Apply is flipped ON, the scraper panel starts a batch and passes its task id. */
  kickTaskId?: string | null;
}

export function KanbanBoard({
  reloadToken = 0,
  postedFilterMinutes = null,
  kickTaskId = null,
}: KanbanBoardProps) {
  const { t } = useTranslations();
  const reducedMotion = useReducedMotion();
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );

  const [columns, setColumns] = useState<ApplicationColumns>(emptyColumns);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [openCardId, setOpenCardId] = useState<string | null>(null);
  const [manualAddOpen, setManualAddOpen] = useState(false);
  const [manageOpen, setManageOpen] = useState(false);
  const [hiddenColumns, setHiddenColumns] = useState<Set<BoardColumnId>>(() =>
    readHiddenColumns()
  );
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [progressOpen, setProgressOpen] = useState(false);
  const [batchRunning, setBatchRunning] = useState(false);
  const [info, setInfo] = useState<string | null>(null);
  const [masterResumeId, setMasterResumeId] = useState<string | null>(null);
  const [tailorPromptId, setTailorPromptId] = useState<string>('full');

  // Persist on an actual change only — an effect keyed on the state would also
  // write the just-read value straight back on mount.
  const handleToggleColumn = (columnId: BoardColumnId) => {
    const next = toggleHiddenColumn(hiddenColumns, columnId);
    // Refused (last visible stage): identical instance, nothing to store.
    if (next === hiddenColumns) return;
    setHiddenColumns(next);
    writeHiddenColumns(next);
  };

  // Horizontal-scroll affordance: the seven stages overflow the canvas, so we
  // track whether more columns sit off-screen and surface controls + a stage
  // rail so no section is ever silently lost beyond the edge.
  const scrollRef = useRef<HTMLDivElement>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await listApplications();
      // Ensure all seven keys exist even if the server omits an empty one.
      setColumns({ ...emptyColumns(), ...data.columns });
      setError(null);
    } catch {
      setError(t('tracker.errors.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  const handleBatchProgress = useCallback(() => {
    void load();
  }, [load]);

  const handleBatchComplete = useCallback(
    (task?: PipelineTask | null) => {
      if (task?.follow_up_task_id) {
        setActiveTaskId(task.follow_up_task_id);
        setProgressOpen(true);
        setBatchRunning(true);
        setInfo('Auto-applying tailored resumes one at a time.');
        void load();
        return;
      }
      setBatchRunning(false);
      void load();
    },
    [load]
  );

  useEffect(() => {
    if (!kickTaskId) return;
    setActiveTaskId(kickTaskId);
    setProgressOpen(true);
    setBatchRunning(true);
    setInfo('Auto-applying tailored resumes one at a time.');
  }, [kickTaskId]);

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadToken]);

  const allCards: Application[] = useMemo(
    () => APPLICATION_STATUS_ORDER.flatMap((status) => columns[status]),
    [columns]
  );

  const latestBatch = useMemo(
    () => latestScrapeBatch(allCards.map((card) => card.notes)),
    [allCards]
  );

  const visibleColumns: ApplicationColumns = useMemo(() => {
    const next = emptyColumns();
    for (const status of APPLICATION_STATUS_ORDER) {
      let cards = columns[status];
      if (postedFilterMinutes != null) {
        cards = cards.filter((card) =>
          postedWithinMinutes(parseScrapeMeta(card.notes).postedAt, postedFilterMinutes)
        );
      }
      if (status === 'saved') {
        cards = [...cards].sort((a, b) => {
          const aMeta = parseScrapeMeta(a.notes);
          const bMeta = parseScrapeMeta(b.notes);
          const aNew = latestBatch && aMeta.scrapeBatch === latestBatch ? 0 : 1;
          const bNew = latestBatch && bMeta.scrapeBatch === latestBatch ? 0 : 1;
          if (aNew !== bNew) return aNew - bNew;
          const aPosted = aMeta.postedAt || a.created_at;
          const bPosted = bMeta.postedAt || b.created_at;
          return bPosted.localeCompare(aPosted);
        });
      }
      next[status] = cards;
    }
    return next;
  }, [columns, postedFilterMinutes, latestBatch]);

  const allIds = useMemo(() => allCards.map((c) => c.application_id), [allCards]);
  const { atsMap } = useAtsMap(allIds);

  // Load pipeline config once so bulk tailor knows which master resume + prompt to use.
  useEffect(() => {
    void (async () => {
      try {
        const cfg = await getPipelineConfig();
        setMasterResumeId(cfg.master_resume_id);
        setTailorPromptId(cfg.tailor_prompt_id ?? 'full');
      } catch {
        // Non-fatal — bulk tailor will show an error if master_resume_id missing.
      }
    })();
  }, []);

  // Eligibility counts: Tailor runs on JD-column cards (status=saved); Apply runs on
  // Tailored-column cards (status=applied) that already have a tailored resume attached.
  const tailorableCount = useMemo(() => {
    let n = 0;
    for (const card of columns.saved ?? []) {
      if (!selectedIds.has(card.application_id)) continue;
      const info = atsMap[card.application_id];
      if (info?.supported) n += 1;
    }
    return n;
  }, [columns.saved, selectedIds, atsMap]);

  const applicableCount = useMemo(() => {
    let n = 0;
    for (const card of columns.applied ?? []) {
      if (!selectedIds.has(card.application_id)) continue;
      if (!card.resume_id) continue;
      const info = atsMap[card.application_id];
      if (info?.supported) n += 1;
    }
    return n;
  }, [columns.applied, selectedIds, atsMap]);

  const duplicateIds = useMemo(() => {
    const groups = new Map<string, string[]>();
    for (const card of allCards) {
      const fingerprint = cardFingerprint(card.company, card.role);
      if (!fingerprint) continue;
      const list = groups.get(fingerprint) ?? [];
      list.push(card.application_id);
      groups.set(fingerprint, list);
    }
    const ids = new Set<string>();
    for (const list of groups.values()) {
      if (list.length < 2) continue;
      for (const id of list) ids.add(id);
    }
    return ids;
  }, [allCards]);

  const collectEligible = (status: ApplicationStatus, needsResume: boolean): string[] => {
    const ids: string[] = [];
    for (const card of columns[status] ?? []) {
      if (!selectedIds.has(card.application_id)) continue;
      if (needsResume && !card.resume_id) continue;
      const cardAts = atsMap[card.application_id];
      if (cardAts?.supported) ids.push(card.application_id);
    }
    return ids;
  };

  const resolveMasterResumeId = async (): Promise<string | null> => {
    if (masterResumeId) return masterResumeId;
    try {
      const list = await fetchResumeList(true);
      const ready = list.filter((r) => r.is_master && r.processing_status === 'ready');
      const master =
        ready.find((r) => r.is_default_master) ?? ready[0];
      if (master) {
        setMasterResumeId(master.resume_id);
        return master.resume_id;
      }
    } catch {
      /* fall through */
    }
    return null;
  };

  const handleBulkTailor = async () => {
    if (batchRunning) return;
    const selected = (columns.saved ?? []).filter((card) =>
      selectedIds.has(card.application_id)
    );
    const { ids, skippedDup } = pickUniqueTailorIds(
      selected,
      columns.applied ?? [],
      atsMap
    );
    if (ids.length === 0) {
      setError(
        skippedDup
          ? 'Those JDs already have a tailored twin. Delete the duplicate card or pick different jobs.'
          : 'No supported JDs in the selection.'
      );
      return;
    }
    const master = await resolveMasterResumeId();
    if (!master) {
      setError('No master resume configured. Upload a master resume first.');
      return;
    }
    try {
      setBatchRunning(true);
      const task = await bulkTailor(ids, master, tailorPromptId, 10);
      setActiveTaskId(task.task_id);
      setProgressOpen(true);
      setError(null);
      setInfo(
        skippedDup
          ? `Queued ${ids.length} job${ids.length === 1 ? '' : 's'} one at a time. Skipped ${skippedDup} duplicate${skippedDup === 1 ? '' : 's'}.`
          : `Queued ${ids.length} job${ids.length === 1 ? '' : 's'} one at a time. The local model will not run them in parallel.`
      );
    } catch (e) {
      setBatchRunning(false);
      setError(e instanceof Error ? e.message : 'Bulk tailor failed');
    }
  };

  const handleBulkApply = async () => {
    if (batchRunning) return;
    const ids = collectEligible('applied', true);
    if (ids.length === 0) return;
    try {
      setBatchRunning(true);
      const task = await bulkApply(ids);
      setActiveTaskId(task.task_id);
      setProgressOpen(true);
      setInfo(`Applying ${ids.length} job${ids.length === 1 ? '' : 's'} one at a time.`);
    } catch (e) {
      setBatchRunning(false);
      setError(e instanceof Error ? e.message : 'Bulk apply failed');
    }
  };

  const visibleStatuses = useMemo(
    () =>
      APPLICATION_STATUS_ORDER.filter((status) => {
        if (status === 'saved') {
          return SENIORITY_ORDER.some((level) => !hiddenColumns.has(`saved:${level}`));
        }
        return !hiddenColumns.has(status);
      }),
    [hiddenColumns]
  );

  const toggleSelectColumn = (ids: string[]) => {
    if (ids.length === 0) return;
    setSelectedIds((prev) => {
      const next = new Set(prev);
      const allOn = ids.every((id) => next.has(id));
      if (allOn) {
        for (const id of ids) next.delete(id);
      } else {
        for (const id of ids) next.add(id);
      }
      return next;
    });
  };

  const boardLanes: BoardLane[] = useMemo(() => {
    const lanes: BoardLane[] = [];
    for (const status of visibleStatuses) {
      if (status === 'saved') {
        for (const level of SENIORITY_ORDER) {
          const key = `saved:${level}` as const;
          if (hiddenColumns.has(key)) continue;
          const applications = visibleColumns.saved.filter(
            (card) => cardSeniority(card.role, card.notes) === level
          );
          lanes.push({
            key,
            status,
            seniority: level,
            title: `${t('tracker.columns.saved')}-${level}`,
            applications,
            droppableId: `column:saved:${level}`,
          });
        }
      } else {
        lanes.push({
          key: status,
          status,
          title: t(`tracker.columns.${status}`),
          applications: visibleColumns[status],
          droppableId: `column:${status}`,
        });
      }
    }
    return lanes;
  }, [visibleStatuses, visibleColumns, hiddenColumns, t]);

  // Master resume ids that back more than one card → "shared resume" badge.
  const sharedResumeIds = useMemo(() => {
    const counts = new Map<string, number>();
    for (const card of allCards) {
      if (card.master_resume_id) {
        counts.set(card.master_resume_id, (counts.get(card.master_resume_id) ?? 0) + 1);
      }
    }
    return new Set([...counts.entries()].filter(([, n]) => n > 1).map(([id]) => id));
  }, [allCards]);

  const isEmpty = allCards.length === 0;

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    // Board width is driven by the seven fixed-width columns, so we only need to
    // (re)attach when the board appears — not on every card-list change.
    const sync = () => {
      setCanScrollLeft(el.scrollLeft > 4);
      setCanScrollRight(Math.ceil(el.scrollLeft + el.clientWidth) < el.scrollWidth - 4);
    };
    sync();
    el.addEventListener('scroll', sync, { passive: true });
    window.addEventListener('resize', sync);
    return () => {
      el.removeEventListener('scroll', sync);
      window.removeEventListener('resize', sync);
    };
  }, [loading, isEmpty, boardLanes.length]);

  const scrollByColumn = (direction: 1 | -1) => {
    scrollRef.current?.scrollBy({
      left: direction * 320,
      behavior: reducedMotion ? 'auto' : 'smooth',
    });
  };

  const scrollToColumn = (laneKey: string) => {
    scrollRef.current?.querySelector<HTMLElement>(`[data-column="${laneKey}"]`)?.scrollIntoView({
      behavior: reducedMotion ? 'auto' : 'smooth',
      inline: 'center',
      block: 'nearest',
    });
  };

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over) return;
    const plan = planMove(columns, String(active.id), String(over.id));
    if (!plan) return;

    // Optimistic update. If the server rejects the move we re-load authoritative
    // state from the server rather than reverting to a captured snapshot, which
    // could be stale if another move/refresh landed in the meantime.
    setColumns(plan.next);
    updateApplication(String(active.id), { status: plan.status, position: plan.position }).catch(
      async () => {
        // Re-sync authoritative state, THEN show a generic failure message:
        // load() clears the error on success, so set it afterwards to keep it
        // visible. Never echo raw backend error text (it could leak secrets).
        await load();
        setError(t('tracker.errors.moveFailed'));
      }
    );
  };

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const nextSet = new Set(prev);
      if (nextSet.has(id)) nextSet.delete(id);
      else nextSet.add(id);
      return nextSet;
    });
  };

  const clearSelection = () => setSelectedIds(new Set());

  const handleBulkMove = async (status: ApplicationStatus) => {
    const ids = [...selectedIds];
    if (ids.length === 0) return;
    try {
      await bulkUpdateStatus(ids, status);
      clearSelection();
      await load();
    } catch {
      setError(t('tracker.errors.moveFailed'));
    }
  };

  const handleBulkDelete = async () => {
    const ids = [...selectedIds];
    if (ids.length === 0) return;
    try {
      await bulkDeleteApplications(ids);
      clearSelection();
      await load();
    } catch {
      setError(t('tracker.errors.deleteFailed'));
    }
  };

  const showScrollControls = !isEmpty && (canScrollLeft || canScrollRight);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Header — a compact single-row toolbar (not the default PageHeader):
          this is a full-height working view, so the band stays short and the
          board keeps the height. Wraps onto more rows on narrow screens. */}
      <header className="flex shrink-0 flex-wrap items-center gap-x-6 gap-y-3 border-b border-ink px-6 py-3">
        <Link href="/dashboard" className={buttonClass({ variant: 'outline', size: 'sm' })}>
          <ArrowLeft aria-hidden="true" />
          {t('nav.backToDashboard')}
        </Link>
        {/* min-w keeps the title readable: with a 0 basis the actions would
            never wrap and would squeeze the title to nothing. */}
        <div className="flex min-w-[14rem] flex-1 flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="font-serif text-2xl font-bold uppercase leading-tight tracking-tight text-ink md:text-3xl">
            {t('tracker.title')}
          </h1>
          <p className="font-mono text-xs uppercase tracking-wide text-steel">
            {'// '}
            {t('tracker.subtitle')}
          </p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-3">
          <Button type="button" variant="outline" size="sm" onClick={() => setManageOpen(true)}>
            <Gear aria-hidden="true" />
            {t('tracker.manage')}
          </Button>
          {showScrollControls && (
            <div className="flex items-center gap-3">
              <Button
                type="button"
                variant="outline"
                size="icon-sm"
                aria-label={t('tracker.scroll.prev')}
                onClick={() => scrollByColumn(-1)}
                disabled={!canScrollLeft}
              >
                <CaretLeft aria-hidden="true" />
              </Button>
              <Button
                type="button"
                variant="outline"
                size="icon-sm"
                aria-label={t('tracker.scroll.next')}
                onClick={() => scrollByColumn(1)}
                disabled={!canScrollRight}
              >
                <CaretRight aria-hidden="true" />
              </Button>
            </div>
          )}
          <Button type="button" size="sm" onClick={() => setManualAddOpen(true)}>
            <Plus aria-hidden="true" />
            {t('tracker.addApplication')}
          </Button>
        </div>
      </header>

      {error && (
        <div className="shrink-0 border-b border-ink px-6 py-3 md:px-8">
          <Alert tone="error">{error}</Alert>
        </div>
      )}

      {info && !error && (
        <div className="shrink-0 border-b border-ink px-6 py-3 md:px-8">
          <Alert tone="info">{info}</Alert>
        </div>
      )}

      {selectedIds.size > 0 && (
        <div className="shrink-0 border-b border-ink px-6 py-3 md:px-8">
          <BulkActionBar
            selectedCount={selectedIds.size}
            tailorableCount={tailorableCount}
            applicableCount={applicableCount}
            onMove={handleBulkMove}
            onDelete={handleBulkDelete}
            onClear={clearSelection}
            onBulkTailor={handleBulkTailor}
            onBulkApply={handleBulkApply}
            batchRunning={batchRunning}
          />
        </div>
      )}

      {/* Board — flexes to fill the remaining canvas height; columns scroll
          horizontally as a group and vertically within each stage. */}
      <div className="flex min-h-0 flex-1 flex-col">
        {loading ? (
          <div role="status" className="flex flex-1 items-center justify-center">
            <SpinnerGap aria-hidden="true" className="size-6 animate-spin text-steel" />
            <span className="sr-only">{t('common.loading')}</span>
          </div>
        ) : isEmpty ? (
          <EmptyState
            title={t('tracker.empty.title')}
            description={t('tracker.empty.description')}
            className="p-6 md:p-8"
          />
        ) : (
          <DndContext
            sensors={sensors}
            collisionDetection={closestCorners}
            onDragEnd={handleDragEnd}
          >
            <div ref={scrollRef} className="flex min-h-0 flex-1 overflow-x-auto">
              {boardLanes.map((lane, index) => (
                <div
                  key={lane.key}
                  data-column={lane.key}
                  className={`flex ${index < boardLanes.length - 1 ? 'border-r border-ink' : ''}`}
                >
                  <KanbanColumn
                    status={lane.status}
                    title={lane.title}
                    droppableId={lane.droppableId}
                    applications={lane.applications}
                    selectedIds={selectedIds}
                    sharedResumeIds={sharedResumeIds}
                    latestBatch={latestBatch}
                    duplicateIds={duplicateIds}
                    atsMap={atsMap}
                    onToggleSelect={toggleSelect}
                    onToggleSelectAll={
                      lane.status === 'saved'
                        ? () => toggleSelectColumn(lane.applications.map((a) => a.application_id))
                        : undefined
                    }
                    onOpen={setOpenCardId}
                  />
                </div>
              ))}
            </div>
          </DndContext>
        )}
      </div>

      {/* Stage rail — an always-visible map of every stage (with counts) so
          off-screen sections are never lost; click a stage to jump to it. */}
      {!isEmpty && (
        <div className="flex shrink-0 items-center gap-3 overflow-x-auto border-t border-ink bg-paper px-6 py-2 md:px-8">
          {canScrollRight && (
            <span className="flex shrink-0 items-center gap-1 font-mono text-xs font-bold uppercase tracking-wide text-primary">
              {t('tracker.scroll.hint')}
              <CaretRight aria-hidden="true" className="size-4" />
            </span>
          )}
          <div className="flex items-center gap-2">
            {boardLanes.map((lane) => (
              <button
                key={lane.key}
                type="button"
                onClick={() => scrollToColumn(lane.key)}
                className={buttonClass({ variant: 'outline', size: 'sm', className: 'shrink-0' })}
              >
                {lane.title}
                <span className="text-ink-soft tabular-nums">{lane.applications.length}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      <CardDetailModal
        applicationId={openCardId}
        open={openCardId !== null}
        onOpenChange={(open) => {
          if (!open) setOpenCardId(null);
        }}
        onUpdated={load}
      />

      <ManualAddApplicationDialog
        open={manualAddOpen}
        onOpenChange={setManualAddOpen}
        onCreated={load}
      />

      <ManageColumnsDialog
        open={manageOpen}
        onOpenChange={setManageOpen}
        hiddenColumns={hiddenColumns}
        onToggle={handleToggleColumn}
      />

      <PipelineProgressModal
        taskId={activeTaskId}
        open={progressOpen}
        onOpenChange={setProgressOpen}
        onComplete={handleBatchComplete}
        onProgress={handleBatchProgress}
      />
    </div>
  );
}
