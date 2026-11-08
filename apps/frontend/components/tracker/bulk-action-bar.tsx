'use client';

import React, { useState } from 'react';
import { X, Trash, MagicWand, PaperPlaneTilt } from '@phosphor-icons/react';
import { Button } from '@/components/ui/button';
import { Dropdown } from '@/components/ui/dropdown';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { useTranslations } from '@/lib/i18n';
import { APPLICATION_STATUS_ORDER, type ApplicationStatus } from '@/lib/api/tracker';

interface BulkActionBarProps {
  selectedCount: number;
  /** Number of selected cards eligible for bulk tailor (status=saved + supported ATS). */
  tailorableCount?: number;
  /** Number of selected cards eligible for bulk apply (status=applied + has resume + supported ATS). */
  applicableCount?: number;
  onMove: (status: ApplicationStatus) => void;
  onDelete: () => void;
  onClear: () => void;
  onBulkTailor?: () => void;
  onBulkApply?: () => void;
  /** True while a sequential bulk tailor/apply queue is already running. */
  batchRunning?: boolean;
}

export function BulkActionBar({
  selectedCount,
  tailorableCount = 0,
  applicableCount = 0,
  onMove,
  onDelete,
  onClear,
  onBulkTailor,
  onBulkApply,
  batchRunning = false,
}: BulkActionBarProps) {
  const { t } = useTranslations();
  const [confirmDelete, setConfirmDelete] = useState(false);

  const moveOptions = APPLICATION_STATUS_ORDER.map((status) => ({
    id: status,
    label: t(`tracker.columns.${status}`),
  }));

  return (
    <div className="flex flex-wrap items-center gap-3">
      <span className="font-mono text-sm font-bold text-ink tabular-nums">
        {t('tracker.bulk.selected', { count: String(selectedCount) })}
      </span>

      <div className="w-48">
        <Dropdown
          options={moveOptions}
          value=""
          placeholder={t('tracker.bulk.moveTo')}
          onChange={(value) => onMove(value as ApplicationStatus)}
        />
      </div>

      {/* Bulk Tailor — only shown when any JD-column cards with supported ATS are selected */}
      {onBulkTailor && tailorableCount > 0 && (
        <div className="flex flex-col gap-0.5">
          <Button
            type="button"
            variant="default"
            size="sm"
            onClick={onBulkTailor}
            disabled={batchRunning}
            title={
              batchRunning
                ? 'A batch is already running one job at a time'
                : `Queue ${tailorableCount} JD${tailorableCount === 1 ? '' : 's'} — one at a time, so the local model stays alive`
            }
          >
            <MagicWand aria-hidden="true" />
            {batchRunning ? 'Queue running…' : `Tailor ${tailorableCount}`}
          </Button>
          <span className="font-mono text-[9px] uppercase tracking-wide text-steel">
            one at a time
          </span>
        </div>
      )}

      {/* Bulk Auto-Apply — only shown when any Tailored cards with supported ATS are selected */}
      {onBulkApply && applicableCount > 0 && (
        <Button
          type="button"
          variant="default"
          size="sm"
          onClick={onBulkApply}
          title={`Auto-apply ${applicableCount} tailored card${applicableCount === 1 ? '' : 's'}`}
        >
          <PaperPlaneTilt aria-hidden="true" />
          Apply {applicableCount}
        </Button>
      )}

      <Button type="button" variant="destructive" size="sm" onClick={() => setConfirmDelete(true)}>
        <Trash aria-hidden="true" />
        {t('common.delete')}
      </Button>

      <Button type="button" variant="ghost" size="sm" onClick={onClear}>
        <X aria-hidden="true" />
        {t('tracker.bulk.clear')}
      </Button>

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={t('tracker.bulk.deleteConfirmTitle')}
        description={t('tracker.bulk.deleteConfirmDescription', { count: String(selectedCount) })}
        confirmLabel={t('common.delete')}
        variant="danger"
        onConfirm={() => {
          setConfirmDelete(false);
          onDelete();
        }}
      />
    </div>
  );
}
