'use client';

import React, { useEffect, useRef } from 'react';
import { useDroppable } from '@dnd-kit/core';
import { SortableContext, verticalListSortingStrategy } from '@dnd-kit/sortable';
import { AnimatePresence, FadeItem } from '@/components/common/presence';
import { EmptyState } from '@/components/ui/empty-state';
import { PanelHeader } from '@/components/ui/panel-header';
import { useTranslations } from '@/lib/i18n';
import type { Application, ApplicationStatus } from '@/lib/api/tracker';
import type { AtsMap } from '@/lib/api/pipeline';
import { parseScrapeMeta } from '@/lib/scrape-meta';
import { ApplicationCard } from './application-card';

const FOCUS_RING =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-white';

interface KanbanColumnProps {
  status: ApplicationStatus;
  applications: Application[];
  selectedIds: Set<string>;
  sharedResumeIds: Set<string>;
  latestBatch?: string | null;
  duplicateIds?: Set<string>;
  atsMap?: AtsMap;
  /** Override the column heading (e.g. JD-Senior). Defaults to the status label. */
  title?: string;
  /** Droppable id. Defaults to ``column:<status>``. JD lanes use ``column:saved:Senior``. */
  droppableId?: string;
  /** When set, the header shows a checkbox that selects every card in this column. */
  onToggleSelectAll?: () => void;
  onToggleSelect: (id: string) => void;
  onOpen: (id: string) => void;
}

export function KanbanColumn({
  status,
  applications,
  selectedIds,
  sharedResumeIds,
  latestBatch,
  duplicateIds,
  atsMap,
  title,
  droppableId,
  onToggleSelectAll,
  onToggleSelect,
  onOpen,
}: KanbanColumnProps) {
  const { t } = useTranslations();
  const heading = title ?? t(`tracker.columns.${status}`);
  const { setNodeRef, isOver } = useDroppable({ id: droppableId ?? `column:${status}` });

  const ids = applications.map((a) => a.application_id);
  const selectedCount = ids.filter((id) => selectedIds.has(id)).length;
  const allSelected = ids.length > 0 && selectedCount === ids.length;
  const someSelected = selectedCount > 0 && !allSelected;
  const checkRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (checkRef.current) checkRef.current.indeterminate = someSelected;
  }, [someSelected]);

  return (
    <div className="flex h-full w-80 shrink-0 flex-col p-3">
      <PanelHeader tone="neutral" level="h2" title={heading}>
        {onToggleSelectAll && (
          <input
            ref={checkRef}
            type="checkbox"
            checked={allSelected}
            disabled={ids.length === 0}
            onChange={onToggleSelectAll}
            aria-label={t('tracker.column.selectAll', { title: heading })}
            className={`size-4 shrink-0 accent-ink disabled:opacity-40 ${FOCUS_RING}`}
          />
        )}
        <span className="font-mono text-xs text-steel tabular-nums">{applications.length}</span>
      </PanelHeader>

      <SortableContext
        items={applications.map((a) => a.application_id)}
        strategy={verticalListSortingStrategy}
      >
        <div
          ref={setNodeRef}
          className={`flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-1 transition-colors ${isOver ? 'bg-paper' : ''}`}
        >
          {applications.length === 0 && (
            <EmptyState variant="framed" title={t('tracker.columns.empty')} />
          )}
          {/* Cards fade in and out; the dnd-kit node stays inside FadeItem
              because dnd-kit owns `transform` on its own element. */}
          <AnimatePresence initial={false}>
            {applications.map((application) => (
              <FadeItem key={application.application_id}>
                <ApplicationCard
                  application={application}
                  selected={selectedIds.has(application.application_id)}
                  thisPull={
                    Boolean(latestBatch) &&
                    parseScrapeMeta(application.notes).scrapeBatch === latestBatch
                  }
                  duplicate={Boolean(duplicateIds?.has(application.application_id))}
                  sharedResume={
                    application.master_resume_id !== null &&
                    sharedResumeIds.has(application.master_resume_id)
                  }
                  atsInfo={atsMap?.[application.application_id]}
                  onToggleSelect={onToggleSelect}
                  onOpen={onOpen}
                />
              </FadeItem>
            ))}
          </AnimatePresence>
        </div>
      </SortableContext>
    </div>
  );
}
