'use client';

import React, { useEffect, useRef, useState } from 'react';
import {
  CheckCircle,
  SpinnerGap,
  XCircle,
  Warning,
  CircleNotch,
  Clock,
  MagicWand,
  PaperPlaneTilt,
} from '@phosphor-icons/react';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { getPipelineTask, type PipelineItem, type PipelineTask } from '@/lib/api/pipeline';

const POLL_MS = 3000;

const STATUS_ICON = {
  pending: <CircleNotch size={13} className="text-steel" />,
  queued: <Clock size={13} className="text-steel" />,
  tailoring: <SpinnerGap size={13} className="animate-spin text-primary" />,
  tailored: <CheckCircle size={13} className="text-success" />,
  applying: <SpinnerGap size={13} className="animate-spin text-primary" />,
  needs_review: <Warning size={13} className="text-warning-text" />,
  applied: <CheckCircle size={13} className="text-success" />,
  failed: <XCircle size={13} className="text-destructive" />,
  skipped: <XCircle size={13} className="text-steel" />,
} as const;

const STATUS_LABEL: Record<string, string> = {
  pending: 'Pending',
  queued: 'Queued',
  tailoring: 'Tailoring…',
  tailored: 'Tailored',
  applying: 'Applying…',
  needs_review: 'Needs Review',
  applied: 'Applied',
  failed: 'Failed',
  skipped: 'Skipped',
};

interface Props {
  taskId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onComplete?: () => void;
}

export function PipelineProgressModal({ taskId, open, onOpenChange, onComplete }: Props) {
  const [task, setTask] = useState<PipelineTask | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const completeFiredRef = useRef(false);

  // Poll task status while modal is open and task not complete
  useEffect(() => {
    if (!open || !taskId) {
      setTask(null);
      setError(null);
      completeFiredRef.current = false;
      return;
    }

    const poll = async () => {
      try {
        const t = await getPipelineTask(taskId);
        if (!t) {
          setError('Task not found (backend may have restarted)');
          return;
        }
        setTask(t);
        if (t.completed_at && !completeFiredRef.current) {
          completeFiredRef.current = true;
          onComplete?.();
          if (pollRef.current) clearInterval(pollRef.current);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    };

    void poll();
    pollRef.current = setInterval(poll, POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [open, taskId, onComplete]);

  if (!taskId) return null;

  const kind = task?.kind ?? 'tailor';
  const total = task?.total ?? 0;
  const counts = task?.counts ?? {};
  const done =
    (counts.tailored ?? 0) + (counts.applied ?? 0) + (counts.failed ?? 0) + (counts.skipped ?? 0);
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  const isDone = Boolean(task?.completed_at);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {kind === 'tailor' ? <MagicWand size={18} /> : <PaperPlaneTilt size={18} />}
            Bulk {kind === 'tailor' ? 'Tailoring' : 'Applying'}
          </DialogTitle>
          <DialogDescription>
            {task ? `${done}/${total} complete · ${pct}%` : 'Loading…'}
          </DialogDescription>
        </DialogHeader>

        <DialogBody className="space-y-3">
          {error && (
            <div className="border border-destructive bg-destructive-tint p-2 font-mono text-xs text-destructive">
              {error}
            </div>
          )}

          {/* Progress bar */}
          {task && (
            <div className="h-2 border border-ink bg-paper">
              <div
                className="h-full bg-primary transition-all duration-500"
                style={{ width: `${pct}%` }}
              />
            </div>
          )}

          {/* Count summary */}
          {task && Object.keys(counts).length > 0 && (
            <div className="flex flex-wrap gap-2 font-mono text-[10px]">
              {Object.entries(counts).map(([status, count]) => (
                <span
                  key={status}
                  className="inline-flex items-center gap-1 border border-ink bg-paper px-1.5 py-0.5"
                >
                  {STATUS_ICON[status as keyof typeof STATUS_ICON] ?? null}
                  <span className="uppercase">{STATUS_LABEL[status] ?? status}</span>
                  <span className="font-bold tabular-nums">{count}</span>
                </span>
              ))}
            </div>
          )}

          {/* Per-item list */}
          {task && task.items.length > 0 && (
            <div className="max-h-80 overflow-y-auto border border-ink bg-canvas">
              {task.items.map((item: PipelineItem) => (
                <div
                  key={item.application_id}
                  className="flex items-center gap-2 border-b border-ink last:border-b-0 px-2 py-1.5 font-mono text-xs"
                >
                  <span className="shrink-0">{STATUS_ICON[item.status] ?? null}</span>
                  <span className="flex-1 min-w-0 truncate">
                    <span className="font-semibold text-ink">{item.company || '—'}</span>
                    <span className="text-steel ml-1">— {item.role || item.ats}</span>
                  </span>
                  <span className="text-steel uppercase text-[10px] shrink-0">
                    {item.ats || '?'}
                  </span>
                  {item.error && (
                    <span
                      className="text-destructive text-[10px] truncate max-w-32"
                      title={item.error}
                    >
                      {item.error}
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </DialogBody>

        <DialogFooter>
          <Button
            type="button"
            variant={isDone ? 'default' : 'outline'}
            onClick={() => onOpenChange(false)}
          >
            {isDone ? 'Done' : 'Hide (keeps running)'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
