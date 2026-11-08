'use client';

import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { SpinnerGap, PencilSimple, MagicWand, Eye, PaperPlaneTilt } from '@phosphor-icons/react';
import { ApplyReviewModal } from './apply-review-modal';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { Alert } from '@/components/ui/alert';
import { StatusIndicator } from '@/components/ui/status-indicator';
import { useTranslations } from '@/lib/i18n';
import { formatDate } from '@/lib/format-date';
import { formatPostedAgo, parseScrapeMeta } from '@/lib/scrape-meta';
import { getApplicationDetail, updateApplication, type ApplicationDetail } from '@/lib/api/tracker';

interface CardDetailModalProps {
  applicationId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onUpdated: () => void;
}

export function CardDetailModal({
  applicationId,
  open,
  onOpenChange,
  onUpdated,
}: CardDetailModalProps) {
  const { t, locale } = useTranslations();
  const router = useRouter();
  const [detail, setDetail] = useState<ApplicationDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [notes, setNotes] = useState('');
  const [savingNotes, setSavingNotes] = useState(false);
  const [notesError, setNotesError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !applicationId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    getApplicationDetail(applicationId)
      .then((data) => {
        if (cancelled) return;
        setDetail(data);
        setNotes(data.notes ?? '');
        setNotesError(null);
      })
      .catch(() => {
        if (!cancelled) setDetail(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, applicationId]);

  // Keep textarea Enter from bubbling to dialog/global handlers.
  const handleNotesKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter') e.stopPropagation();
  };

  const handleSaveNotes = async () => {
    if (!applicationId) return;
    setSavingNotes(true);
    setNotesError(null);
    try {
      await updateApplication(applicationId, { notes });
      onUpdated();
    } catch {
      // Show a generic message — never echo raw backend error text inline,
      // which could contain sensitive values.
      setNotesError(t('common.error'));
    } finally {
      setSavingNotes(false);
    }
  };

  const resumeAvailable = Boolean(detail?.resume);
  const [applyOpen, setApplyOpen] = useState(false);

  // Extract apply URL from notes to determine if auto-apply is possible.
  // Auto-apply only works with direct ATS URLs (Greenhouse, Lever, Ashby).
  // Jobright/Wellfound aggregator links can't be used to auto-fill forms.
  const applyUrl = (() => {
    const notes = detail?.notes ?? '';
    const m = notes.match(/^Apply:\s*(https?:\/\/\S+)/im);
    return m ? m[1] : '';
  })();
  const isSupportedApplyUrl = Boolean(
    applyUrl &&
      !/workday|icims\.com|jobvite|linkedin\.com|indeed\.com/i.test(applyUrl)
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>{detail?.company || t('tracker.card.companyUnknown')}</DialogTitle>
          <DialogDescription>{detail?.role || t('tracker.card.roleUnknown')}</DialogDescription>
        </DialogHeader>

        <DialogBody className="space-y-4">
          {loading ? (
            <div role="status" className="py-8">
              <SpinnerGap aria-hidden="true" className="size-5 animate-spin text-steel" />
              <span className="sr-only">{t('common.loading')}</span>
            </div>
          ) : detail ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-3 font-mono text-xs uppercase text-ink-soft">
                <StatusIndicator tone="neutral">
                  {t(`tracker.columns.${detail.status}`)}
                </StatusIndicator>
                {detail.applied_at && (
                  <span className="tabular-nums">
                    {formatDate(detail.applied_at, locale, { month: 'short', year: 'numeric' })}
                  </span>
                )}
                {(() => {
                  const meta = parseScrapeMeta(detail.notes);
                  return (
                    <>
                      {meta.seniority && (
                        <span className="border border-ink px-1 font-bold text-ink">
                          {meta.seniority}
                        </span>
                      )}
                      <span className="tabular-nums" title={meta.postedAt || undefined}>
                        {formatPostedAgo(meta.postedAt, meta.postedLabel)}
                      </span>
                    </>
                  );
                })()}
              </div>

              <div className="space-y-1">
                <Label>{t('tracker.modal.jobDescription')}</Label>
                <div className="max-h-48 overflow-y-auto whitespace-pre-wrap border border-ink bg-canvas p-3 text-sm">
                  {detail.job_content || t('tracker.modal.noJobDescription')}
                </div>
              </div>

              <div className="space-y-1">
                <Label htmlFor="card-notes">{t('tracker.modal.notes')}</Label>
                <Textarea
                  id="card-notes"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  onKeyDown={handleNotesKeyDown}
                  placeholder={t('tracker.modal.notesPlaceholder')}
                  rows={3}
                />
                {notesError && <Alert tone="error">{notesError}</Alert>}
                <div className="flex justify-end">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={handleSaveNotes}
                    disabled={savingNotes}
                  >
                    {savingNotes ? (
                      <>
                        <SpinnerGap aria-hidden="true" className="animate-spin" />
                        {t('common.saving')}
                      </>
                    ) : (
                      t('tracker.modal.saveNotes')
                    )}
                  </Button>
                </div>
              </div>

              {!resumeAvailable && (
                <Alert tone="warning">{t('tracker.modal.resumeUnavailable')}</Alert>
              )}
            </div>
          ) : (
            <p className="py-6 font-mono text-sm text-steel">{t('tracker.modal.loadFailed')}</p>
          )}
        </DialogBody>

        {/* Auto-apply review modal */}
        <ApplyReviewModal
          applicationId={applicationId}
          resumeId={detail?.resume_id ?? null}
          open={applyOpen}
          onOpenChange={setApplyOpen}
          onSubmitted={() => {
            setApplyOpen(false);
            onUpdated();
          }}
        />

        <DialogFooter>
          {/* Auto-Apply — only on tailored cards with a supported ATS URL */}
          {detail && detail.status !== 'saved' && detail.resume_id && detail.job_content && (
            <Button
              type="button"
              variant="default"
              onClick={() => setApplyOpen(true)}
              disabled={!isSupportedApplyUrl}
              title={
                isSupportedApplyUrl
                  ? `Auto-fill and submit via ${applyUrl}`
                  : 'No direct apply URL found (only Greenhouse, Lever, Ashby supported). Run the scraper again to get updated apply links.'
              }
            >
              <PaperPlaneTilt aria-hidden="true" />
              Auto-Apply
            </Button>
          )}

          {/* Review — side-by-side JD + Tailored resume + ATS keyword match score. */}
          {detail && detail.status !== 'saved' && (
            <Button
              type="button"
              variant="outline"
              onClick={() => router.push(`/review/${applicationId}`)}
              disabled={!applicationId}
            >
              <Eye aria-hidden="true" />
              Review
            </Button>
          )}
          {/* Tailor Resume — pre-fills /tailor with this JD so the real LLM-based
              resume tailoring pipeline runs against it. */}
          <Button
            type="button"
            variant={detail?.status === 'saved' ? 'default' : 'outline'}
            onClick={() => {
              if (!detail?.job_content || !applicationId) return;
              const encoded = btoa(unescape(encodeURIComponent(detail.job_content)));
              router.push(`/tailor?jd=${encodeURIComponent(encoded)}&appId=${applicationId}`);
            }}
            disabled={!detail?.job_content || !applicationId}
          >
            <MagicWand aria-hidden="true" />
            {detail?.status === 'saved' ? 'Tailor Resume' : 'Re-tailor'}
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              if (detail?.resume_id) router.push(`/builder?id=${detail.resume_id}`);
            }}
            disabled={!resumeAvailable}
          >
            <PencilSimple aria-hidden="true" />
            {t('tracker.modal.editResume')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
