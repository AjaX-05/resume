'use client';

import React from 'react';
import { useTranslations } from '@/lib/i18n';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { ToggleSwitch } from '@/components/ui/toggle-switch';
import {
  BOARD_COLUMN_ORDER,
  isLastVisibleColumn,
  type BoardColumnId,
} from '@/lib/utils/tracker-column-visibility';

interface ManageColumnsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  hiddenColumns: Set<BoardColumnId>;
  onToggle: (columnId: BoardColumnId) => void;
}

export function columnLabel(columnId: BoardColumnId, t: (key: string) => string): string {
  if (columnId.startsWith('saved:')) {
    return `${t('tracker.columns.saved')}-${columnId.slice('saved:'.length)}`;
  }
  return t(`tracker.columns.${columnId}`);
}

export function ManageColumnsDialog({
  open,
  onOpenChange,
  hiddenColumns,
  onToggle,
}: ManageColumnsDialogProps) {
  const { t } = useTranslations();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="sm">
        <DialogHeader>
          <DialogTitle>{t('tracker.manageDialog.title')}</DialogTitle>
          <DialogDescription>{t('tracker.manageDialog.description')}</DialogDescription>
        </DialogHeader>

        <DialogBody className="space-y-3">
          {BOARD_COLUMN_ORDER.map((columnId) => {
            const hidden = hiddenColumns.has(columnId);
            const lastVisible = isLastVisibleColumn(hiddenColumns, columnId);
            return (
              <div key={columnId}>
                <ToggleSwitch
                  checked={!hidden}
                  onCheckedChange={() => onToggle(columnId)}
                  label={columnLabel(columnId, t)}
                  description={
                    hidden ? t('tracker.manageDialog.hidden') : t('tracker.manageDialog.visible')
                  }
                  disabled={lastVisible}
                />
                {lastVisible && (
                  <p className="mt-1 font-mono text-xs uppercase tracking-wide text-ink-soft">
                    {t('tracker.manageDialog.lastVisibleHint')}
                  </p>
                )}
              </div>
            );
          })}
        </DialogBody>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            {t('tracker.manageDialog.close')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
