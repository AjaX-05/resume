'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { CheckCircle, SpinnerGap, Warning, XCircle, ArrowClockwise } from '@phosphor-icons/react';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Alert } from '@/components/ui/alert';
import {
  startApply,
  getApplyStatus,
  getApplyScreenshot,
  approveApply,
  rejectApply,
  type ApplySession,
  type ApplyStatus,
} from '@/lib/api/applier';

const POLL_MS = 2500;

const STATUS_LABEL: Record<ApplyStatus, string> = {
  pending: 'Starting…',
  filling: 'Filling form fields…',
  needs_review: 'Ready for review',
  submitting: 'Submitting…',
  submitted: 'Submitted',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

interface Props {
  applicationId: string | null;
  resumeId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmitted: () => void;
  /** When false, poll an already-started session (bulk auto-apply) instead of POSTing /applier/start. */
  startOnOpen?: boolean;
}

export function ApplyReviewModal({
  applicationId,
  resumeId,
  open,
  onOpenChange,
  onSubmitted,
  startOnOpen = true,
}: Props) {
  const [session, setSession] = useState<ApplySession | null>(null);
  const [screenshotB64, setScreenshotB64] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startedRef = useRef(false);

  const stopPoll = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const fetchScreenshot = useCallback(async (appId: string) => {
    try {
      const s = await getApplyScreenshot(appId);
      if (s.screenshot_b64) setScreenshotB64(s.screenshot_b64);
    } catch {
      /* silent */
    }
  }, []);

  const pollStatus = useCallback(
    async (appId: string) => {
      try {
        const s = await getApplyStatus(appId);
        if (!s) {
          // 404 — session was never created (apply URL missing, PDF error, etc.)
          stopPoll();
          setError(
            'Could not start the application. Check that this card has a direct apply URL (Greenhouse/Lever) in its notes, not just a Jobright link.'
          );
          return;
        }
        setSession(s);
        if (s.has_screenshot && !screenshotB64) {
          await fetchScreenshot(appId);
        }
        if (['submitted', 'failed', 'cancelled'].includes(s.status)) {
          stopPoll();
          if (s.status === 'submitted') onSubmitted();
        }
      } catch {
        /* silent */
      }
    },
    [screenshotB64, fetchScreenshot, stopPoll, onSubmitted]
  );

  // Start apply when modal opens (single-card flow). Bulk auto-apply already
  // started the Playwright session — just poll it.
  useEffect(() => {
    if (!open || !applicationId || startedRef.current) return;
    if (startOnOpen && !resumeId) return;
    startedRef.current = true;
    setLoading(true);
    setError(null);
    setScreenshotB64(null);
    setSession(null);

    const begin = startOnOpen
      ? startApply(applicationId, resumeId as string)
      : getApplyStatus(applicationId).then((s) => {
          if (!s) throw new Error('Apply session not found');
          return s;
        });

    begin
      .then((s) => {
        setSession(s);
        if (s.has_screenshot) void fetchScreenshot(applicationId);
        pollRef.current = setInterval(() => pollStatus(applicationId), POLL_MS);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => setLoading(false));

    return () => {
      stopPoll();
      startedRef.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, applicationId, resumeId, startOnOpen]);

  // Reset when modal closes
  const handleOpenChange = (v: boolean) => {
    if (!v) {
      stopPoll();
      startedRef.current = false;
      setSession(null);
      setScreenshotB64(null);
      setError(null);
    }
    onOpenChange(v);
  };

  const handleApprove = async () => {
    if (!applicationId) return;
    setLoading(true);
    try {
      const s = await approveApply(applicationId);
      setSession(s);
      if (!pollRef.current) {
        pollRef.current = setInterval(() => pollStatus(applicationId), POLL_MS);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const handleCancel = async () => {
    if (!applicationId) return;
    try {
      await rejectApply(applicationId);
    } catch {
      /* silent */
    }
    handleOpenChange(false);
  };

  const status = session?.status ?? 'pending';
  const isTerminal = ['submitted', 'failed', 'cancelled'].includes(status);
  const needsReview = status === 'needs_review';
  const isActive = ['pending', 'filling', 'submitting'].includes(status);

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Auto-Apply</DialogTitle>
          <DialogDescription>{session?.apply_url ?? 'Preparing application…'}</DialogDescription>
        </DialogHeader>

        <DialogBody className="space-y-4">
          {/* Status bar */}
          <div className="flex items-center gap-2 font-mono text-sm">
            {isActive && <SpinnerGap className="animate-spin text-primary" size={16} />}
            {status === 'needs_review' && (
              <ArrowClockwise className="text-warning-text" size={16} />
            )}
            {status === 'submitted' && <CheckCircle className="text-success" size={16} />}
            {status === 'failed' && <XCircle className="text-destructive" size={16} />}
            {status === 'cancelled' && <XCircle className="text-steel" size={16} />}
            <span>{STATUS_LABEL[status as ApplyStatus]}</span>
          </div>

          {error && <Alert tone="error">{error}</Alert>}

          {/* Fields filled preview */}
          {session && session.filled_fields && Object.keys(session.filled_fields).length > 0 && (
            <div className="border border-ink bg-paper p-3">
              <div className="font-mono text-[10px] uppercase text-steel mb-2">Fields Filled</div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-xs">
                {Object.entries(session.filled_fields).map(([k, v]) => (
                  <div key={k} className="flex gap-1 min-w-0">
                    <span className="text-steel shrink-0">{k.replace(/_/g, ' ')}:</span>
                    <span className="truncate">{String(v).replace(/\/.*/, '')}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Screenshot */}
          {screenshotB64 && (
            <div className="border border-ink overflow-hidden">
              <div className="font-mono text-[10px] uppercase text-steel bg-paper px-3 py-1.5 border-b border-ink">
                Form Preview — Review Before Submitting
              </div>
              <div className="max-h-96 overflow-y-auto bg-white">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={`data:image/png;base64,${screenshotB64}`}
                  alt="Filled application form screenshot"
                  className="w-full"
                />
              </div>
            </div>
          )}

          {needsReview && !screenshotB64 && (
            <div className="flex items-center gap-2 text-steel font-mono text-sm">
              <SpinnerGap className="animate-spin" size={14} />
              Loading form preview…
            </div>
          )}

          {/* Error from session */}
          {session?.error && (
            <Alert tone="error">
              <Warning size={14} className="shrink-0" />
              {session.error}
            </Alert>
          )}

          {status === 'submitted' && (
            <Alert tone="success">
              Application submitted successfully. Card moved to Applied column.
            </Alert>
          )}
        </DialogBody>

        <DialogFooter>
          {needsReview && (
            <>
              <Button variant="default" onClick={handleApprove} disabled={loading}>
                {loading ? (
                  <>
                    <SpinnerGap className="animate-spin" size={14} /> Submitting…
                  </>
                ) : (
                  <>
                    <CheckCircle size={14} /> Submit Application
                  </>
                )}
              </Button>
              <Button variant="outline" onClick={handleCancel} disabled={loading}>
                <XCircle size={14} />
                Cancel
              </Button>
            </>
          )}
          {isTerminal && (
            <Button variant="outline" onClick={() => handleOpenChange(false)}>
              Close
            </Button>
          )}
          {(isActive || (!needsReview && !isTerminal)) && (
            <Button variant="outline" onClick={handleCancel}>
              Cancel
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
