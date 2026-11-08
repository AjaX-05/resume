'use client';

/**
 * Side-by-side review page for a tailored application.
 *
 *   LEFT COLUMN           RIGHT COLUMN
 *   ─────────────         ─────────────
 *   Keyword Match %       Tailored resume preview (paginated)
 *   Overall ATS score     Download PDF button
 *   Missing keywords
 *   ────────────
 *   Full JD text
 *
 * Entry points:
 *   - Dashboard tile for a tailored resume (via resume detail page)
 *   - Tracker card detail modal → "Review" button
 */

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import {
  ArrowLeft,
  DownloadSimple,
  MagicWand,
  SpinnerGap,
  CheckCircle,
  Warning,
} from '@phosphor-icons/react';
import { PageFrame } from '@/components/ui/page-frame';
import { Button } from '@/components/ui/button';
import { Alert } from '@/components/ui/alert';
import { getApplicationDetail, type ApplicationDetail } from '@/lib/api/tracker';
import {
  fetchLastAtsScore,
  recalculateAtsScore,
  fetchJobDescription,
  downloadResumePdf,
  type ATSScoreRecord,
  type ResumeJobDescription,
} from '@/lib/api/resume';
import type { ATSScore } from '@/components/common/resume_previewer_context';
import { type ResumeData } from '@/components/dashboard/resume-component';
import { PaginatedPreview } from '@/components/preview';
import { fetchResume } from '@/lib/api/resume';
import { DEFAULT_TEMPLATE_SETTINGS, type TemplateSettings } from '@/lib/types/template-settings';
import { downloadBlobAsFile, sanitizeFilename } from '@/lib/utils/download';

function scorePill(score: number): string {
  if (score >= 85) return 'bg-success text-white';
  if (score >= 70) return 'bg-primary text-white';
  if (score >= 55) return 'bg-warning text-black';
  return 'bg-destructive text-white';
}

export default function ReviewPage() {
  const params = useParams();
  const router = useRouter();
  const appId = String(params?.appId ?? '');

  const [app, setApp] = useState<ApplicationDetail | null>(null);
  const [jd, setJd] = useState<ResumeJobDescription | null>(null);
  const [ats, setAts] = useState<ATSScore | null>(null);
  const [resumeData, setResumeData] = useState<ResumeData | null>(null);
  const [templateSettings, setTemplateSettings] =
    useState<TemplateSettings>(DEFAULT_TEMPLATE_SETTINGS);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [recalculating, setRecalculating] = useState(false);
  const loadedOnce = useRef(false);

  useEffect(() => {
    if (!appId || loadedOnce.current) return;
    loadedOnce.current = true;

    const load = async () => {
      try {
        const detail = await getApplicationDetail(appId);
        setApp(detail);

        const resumeId = detail.resume_id;

        // Pull in parallel: resume data, JD, cached ATS score
        const [resumeRes, jdRes, atsRes] = await Promise.allSettled([
          fetchResume(resumeId),
          fetchJobDescription(resumeId).catch(() => null),
          fetchLastAtsScore(resumeId).catch(() => null),
        ]);

        if (resumeRes.status === 'fulfilled' && resumeRes.value.processed_resume) {
          setResumeData(resumeRes.value.processed_resume as ResumeData);
          if (resumeRes.value.template_settings) {
            setTemplateSettings(resumeRes.value.template_settings);
          }
        }
        if (jdRes.status === 'fulfilled' && jdRes.value) {
          setJd(jdRes.value);
        }
        if (atsRes.status === 'fulfilled' && atsRes.value) {
          setAts(atsRes.value.score);
        } else {
          // No cached score → compute it now (first visit after tailoring).
          // Fails silently if the resume isn't tailored — we show a CTA instead.
          try {
            const fresh = await recalculateAtsScore(resumeId);
            setAts(fresh.score);
          } catch (e) {
            console.warn('ATS score calc failed (resume may not be tailored yet):', e);
          }
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load review');
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, [appId]);

  const handleDownload = async () => {
    if (!app) return;
    setDownloading(true);
    try {
      const blob = await downloadResumePdf(app.resume_id);
      const filename = sanitizeFilename(
        `${app.company || 'resume'}_${app.role || 'tailored'}`,
        app.resume_id
      );
      downloadBlobAsFile(blob, filename);
    } catch (e) {
      alert(`Download failed: ${e}`);
    } finally {
      setDownloading(false);
    }
  };

  const handleRecalculate = async () => {
    if (!app) return;
    setRecalculating(true);
    try {
      const fresh = await recalculateAtsScore(app.resume_id);
      setAts(fresh.score);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes('only available for tailored')) {
        alert(
          'This card still points at the master resume (never been tailored, or ' +
            'was tailored before the "auto-link tailored resume" fix). ' +
            'Click "Re-tailor" to generate a tailored version — the card will ' +
            'auto-link to it and the ATS score will become available.'
        );
      } else {
        alert(`Recalculate failed: ${msg}`);
      }
    } finally {
      setRecalculating(false);
    }
  };

  const keywordScore = ats?.sub_scores.keyword_match ?? null;
  const overallScore = ats?.overall_score ?? null;
  const missingKeywords = ats?.missing_keywords ?? [];

  return (
    <PageFrame width="wide" className="flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-4 border-b border-ink pb-3">
        <div className="flex items-center gap-3">
          <Button variant="outline" size="sm" onClick={() => router.push('/tracker')}>
            <ArrowLeft size={14} />
            Back to Tracker
          </Button>
          <div>
            <h1 className="font-serif text-2xl font-bold">{app?.company || 'Loading…'}</h1>
            <p className="font-mono text-xs text-steel uppercase">{app?.role ?? ''}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!app?.job_content}
            onClick={() => {
              if (!app?.job_content) return;
              const encoded = btoa(unescape(encodeURIComponent(app.job_content)));
              router.push(`/tailor?jd=${encodeURIComponent(encoded)}&appId=${appId}`);
            }}
          >
            <MagicWand size={14} />
            Re-tailor
          </Button>
          <Button
            variant="default"
            size="sm"
            disabled={downloading || !app}
            onClick={handleDownload}
          >
            {downloading ? (
              <SpinnerGap size={14} className="animate-spin" />
            ) : (
              <DownloadSimple size={14} />
            )}
            Download PDF
          </Button>
        </div>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-steel font-mono text-sm">
          <SpinnerGap className="animate-spin" size={16} /> Loading review…
        </div>
      )}

      {error && <Alert tone="error">{error}</Alert>}

      {!loading && app && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 min-h-0">
          {/* LEFT: scores + JD */}
          <div className="flex flex-col gap-3 min-h-0">
            {/* ATS score card */}
            <div className="border border-ink bg-white p-4 shadow-sw-nested">
              <div className="flex items-center justify-between mb-3">
                <h2 className="font-mono text-xs uppercase font-bold tracking-wide">
                  ATS Match Score
                </h2>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleRecalculate}
                  disabled={recalculating}
                  className="h-6 px-2 text-[10px] font-mono"
                >
                  {recalculating ? <SpinnerGap size={10} className="animate-spin" /> : 'Recalc'}
                </Button>
              </div>

              {ats ? (
                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-2">
                    <div className="border border-ink p-3 bg-paper">
                      <div className="font-mono text-[10px] uppercase text-steel mb-1">
                        Keyword Match
                      </div>
                      <div
                        className={`inline-block px-3 py-1 font-mono font-bold text-2xl ${scorePill(keywordScore ?? 0)}`}
                      >
                        {keywordScore ?? '—'}%
                      </div>
                    </div>
                    <div className="border border-ink p-3 bg-paper">
                      <div className="font-mono text-[10px] uppercase text-steel mb-1">Overall</div>
                      <div
                        className={`inline-block px-3 py-1 font-mono font-bold text-2xl ${scorePill(overallScore ?? 0)}`}
                      >
                        {overallScore ?? '—'}%
                      </div>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs font-mono">
                    <div className="flex justify-between">
                      <span className="text-steel">Skills coverage:</span>
                      <span className="font-bold">{ats.sub_scores.skills_coverage}%</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-steel">Section complete:</span>
                      <span className="font-bold">{ats.sub_scores.section_completeness}%</span>
                    </div>
                    {ats.sub_scores.title_match != null && (
                      <div className="flex justify-between">
                        <span className="text-steel">Title match:</span>
                        <span className="font-bold">{ats.sub_scores.title_match}%</span>
                      </div>
                    )}
                    {ats.sub_scores.date_consistency != null && (
                      <div className="flex justify-between">
                        <span className="text-steel">Dates:</span>
                        <span className="font-bold">{ats.sub_scores.date_consistency}%</span>
                      </div>
                    )}
                  </div>

                  {missingKeywords.length > 0 && (
                    <div>
                      <div className="font-mono text-[10px] uppercase text-steel mb-1 flex items-center gap-1">
                        <Warning size={11} className="text-warning-text" />
                        Missing from resume ({missingKeywords.length})
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {missingKeywords.slice(0, 20).map((kw) => (
                          <span
                            key={kw}
                            className="px-1.5 py-0.5 border border-warning-text text-warning-text text-[10px] font-mono"
                          >
                            {kw}
                          </span>
                        ))}
                        {missingKeywords.length > 20 && (
                          <span className="text-[10px] font-mono text-steel">
                            +{missingKeywords.length - 20} more
                          </span>
                        )}
                      </div>
                    </div>
                  )}

                  {missingKeywords.length === 0 && ats.injectable_keywords?.length === 0 && (
                    <div className="flex items-center gap-2 text-success text-xs font-mono">
                      <CheckCircle size={14} />
                      All JD keywords covered.
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-steel font-mono text-xs">No score yet. Click Recalc.</div>
              )}
            </div>

            {/* JD text */}
            <div className="border border-ink bg-white p-4 shadow-sw-nested flex-1 min-h-0 flex flex-col">
              <h2 className="font-mono text-xs uppercase font-bold tracking-wide mb-2">
                Job Description
              </h2>
              <div className="flex-1 overflow-y-auto whitespace-pre-wrap font-mono text-xs leading-relaxed border border-ink bg-paper p-3">
                {app.job_content || 'No JD stored for this application.'}
              </div>
            </div>
          </div>

          {/* RIGHT: Tailored resume preview */}
          <div className="border border-ink bg-white p-4 shadow-sw-nested flex flex-col min-h-0">
            <div className="flex items-center justify-between mb-2">
              <h2 className="font-mono text-xs uppercase font-bold tracking-wide">
                Tailored Resume
              </h2>
            </div>
            <div className="flex-1 overflow-y-auto border border-ink bg-canvas p-3">
              {resumeData ? (
                <PaginatedPreview resumeData={resumeData} settings={templateSettings} />
              ) : (
                <div className="text-steel font-mono text-xs">Loading resume…</div>
              )}
            </div>
          </div>
        </div>
      )}
    </PageFrame>
  );
}
