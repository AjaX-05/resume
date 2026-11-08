'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Play,
  Clock,
  ArrowsClockwise,
  SpinnerGap,
  Warning,
  CheckCircle,
  DownloadSimple,
} from '@phosphor-icons/react';
import { Button } from '@/components/ui/button';
import {
  runScraper,
  getScraperStatus,
  syncExistingDiscoveries,
  type ScraperSource,
  type ScraperStatus,
} from '@/lib/api/scraper';
import { getPipelineConfig, updatePipelineConfig, type PipelineConfig } from '@/lib/api/pipeline';
import { MagicWand, PaperPlaneTilt, ShieldCheck } from '@phosphor-icons/react';

const AUTO_KEY = 'scraper_auto_enabled';
const POLL_MS = 2000;
const AUTO_INTERVAL_MS = 2 * 60 * 60 * 1000; // 2 hours

interface Props {
  onComplete: () => void;
}

function fmtTime(iso: string | null): string {
  if (!iso) return 'Never';
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function ScraperPanel({ onComplete }: Props) {
  const [status, setStatus] = useState<ScraperStatus>({
    running: false,
    source: null,
    last_run_at: null,
    last_jobs_found: 0,
    last_error: null,
  });
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);
  const [autoEnabled, setAutoEnabled] = useState(() => {
    if (typeof window === 'undefined') return false;
    return localStorage.getItem(AUTO_KEY) === 'true';
  });
  const [elapsed, setElapsed] = useState(0);
  const [showLogs, setShowLogs] = useState(false);
  const [completionToast, setCompletionToast] = useState<string | null>(null);
  const [pipelineConfig, setPipelineConfig] = useState<PipelineConfig | null>(null);
  const [configSaving, setConfigSaving] = useState<string | null>(null);
  const prevRunning = useRef(false);
  const autoTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startedAt = useRef<number | null>(null);

  // Load pipeline config once
  useEffect(() => {
    void (async () => {
      try {
        const cfg = await getPipelineConfig();
        setPipelineConfig(cfg);
      } catch {
        /* ignore */
      }
    })();
  }, []);

  const togglePipelineFlag = async (field: 'auto_tailor' | 'auto_apply' | 'approval_gate') => {
    if (!pipelineConfig) return;
    const next = { ...pipelineConfig, [field]: !pipelineConfig[field] };
    setConfigSaving(field);
    try {
      const saved = await updatePipelineConfig(next);
      setPipelineConfig(saved);
    } catch {
      /* ignore - leave UI unchanged */
    } finally {
      setConfigSaving(null);
    }
  };

  // Poll status always (so we see external runs too)
  useEffect(() => {
    const poll = async () => {
      try {
        const s = await getScraperStatus();
        if (s.running && !prevRunning.current) {
          startedAt.current = Date.now();
        }
        if (!s.running && prevRunning.current) {
          startedAt.current = null;
          // Explicit "Done" toast — confirms the scrape finished and tells the
          // user how many new jobs landed in the JD column (or why it was 0).
          const msg =
            s.last_jobs_found > 0
              ? `Scrape complete — ${s.last_jobs_found} new job${s.last_jobs_found === 1 ? '' : 's'} added to JD column.`
              : s.last_error
                ? `Scrape finished with errors (see logs).`
                : `Scrape complete — no new jobs this cycle (all already seen, or source returned nothing).`;
          setCompletionToast(msg);
          setTimeout(() => setCompletionToast(null), 8000);
          onComplete();
        }
        prevRunning.current = s.running;
        setStatus(s);
      } catch {
        /* ignore */
      }
    };
    void poll();
    const id = setInterval(poll, POLL_MS);
    return () => clearInterval(id);
  }, [onComplete]);

  // Elapsed timer while running
  useEffect(() => {
    if (!status.running) {
      setElapsed(0);
      return;
    }
    const t = setInterval(() => {
      if (startedAt.current) {
        setElapsed(Math.floor((Date.now() - startedAt.current) / 1000));
      }
    }, 1000);
    return () => clearInterval(t);
  }, [status.running]);

  const trigger = useCallback(async (source: ScraperSource) => {
    try {
      startedAt.current = Date.now();
      prevRunning.current = true;
      setStatus((s) => ({ ...s, running: true, source, last_error: null }));
      await runScraper(source);
    } catch (e) {
      setStatus((s) => ({ ...s, running: false, last_error: String(e) }));
    }
  }, []);

  const handleSync = useCallback(async () => {
    setSyncing(true);
    setSyncMsg(null);
    try {
      const r = await syncExistingDiscoveries();
      setSyncMsg(
        `Synced ${r.synced} job${r.synced === 1 ? '' : 's'} (${r.skipped} already in tracker)`
      );
      onComplete();
      setTimeout(() => setSyncMsg(null), 5000);
    } catch (e) {
      setSyncMsg(`Sync failed: ${e}`);
    } finally {
      setSyncing(false);
    }
  }, [onComplete]);

  // Auto-schedule
  useEffect(() => {
    if (autoTimerRef.current) clearTimeout(autoTimerRef.current);
    if (!autoEnabled) return;
    const schedule = () => {
      trigger('all').finally(() => {
        autoTimerRef.current = setTimeout(schedule, AUTO_INTERVAL_MS);
      });
    };
    autoTimerRef.current = setTimeout(schedule, AUTO_INTERVAL_MS);
    return () => {
      if (autoTimerRef.current) clearTimeout(autoTimerRef.current);
    };
  }, [autoEnabled, trigger]);

  const toggleAuto = () => {
    const next = !autoEnabled;
    setAutoEnabled(next);
    localStorage.setItem(AUTO_KEY, String(next));
  };

  return (
    <>
      {/* Prominent LOADING BANNER — fills the top while scraping */}
      {status.running && (
        <div className="flex items-center gap-3 px-4 py-3 border-b-2 border-ink bg-primary text-white animate-pulse">
          <SpinnerGap className="animate-spin shrink-0" size={20} />
          <div className="flex-1 min-w-0">
            <div className="font-mono font-bold text-sm uppercase tracking-wide">
              Scraping {status.source ?? 'all sources'}…
            </div>
            <div className="font-mono text-[11px] opacity-90">
              {elapsed}s elapsed · scraping Jobright + Wellfound + YC Jobs · this may take 5–15 min
              on free tier
            </div>
          </div>
        </div>
      )}

      {/* Sync success message */}
      {syncMsg && (
        <div className="flex items-center gap-2 px-4 py-2 border-b border-ink bg-success-tint text-success">
          <CheckCircle size={14} />
          <span className="font-mono text-xs">{syncMsg}</span>
        </div>
      )}

      {/* Scrape completion toast */}
      {completionToast && !status.running && (
        <div
          className={`flex items-center gap-2 px-4 py-2 border-b border-ink ${status.last_jobs_found > 0 ? 'bg-success-tint text-success' : status.last_error ? 'bg-destructive-tint text-destructive' : 'bg-info-tint text-primary'}`}
        >
          <CheckCircle size={14} />
          <span className="font-mono text-xs flex-1">{completionToast}</span>
          {status.last_log_tail && (
            <button
              type="button"
              className="font-mono text-[10px] underline"
              onClick={() => setShowLogs(!showLogs)}
            >
              {showLogs ? 'Hide logs' : 'View logs'}
            </button>
          )}
        </div>
      )}

      {/* Expanded log viewer */}
      {showLogs && status.last_log_tail && (
        <div className="border-b border-ink bg-canvas px-4 py-2 max-h-64 overflow-y-auto">
          <pre className="font-mono text-[10px] whitespace-pre-wrap text-ink-soft">
            {status.last_log_tail}
          </pre>
        </div>
      )}

      {/* Compact control bar */}
      <div className="flex flex-wrap items-center gap-2 px-4 py-2 border-b border-ink bg-paper">
        {/* Status */}
        <div className="flex items-center gap-1.5 text-xs font-mono text-steel min-w-0">
          <Clock size={13} className="shrink-0" />
          <span>Last: {fmtTime(status.last_run_at)}</span>
          {status.last_jobs_found > 0 && !status.running && (
            <span className="ml-1 px-1.5 py-0.5 bg-success text-white text-[10px] font-mono">
              +{status.last_jobs_found} new
            </span>
          )}
          {status.last_error && !status.running && (
            <span title={status.last_error} className="flex items-center">
              <Warning size={13} className="text-destructive shrink-0" />
            </span>
          )}
        </div>

        <div className="flex-1" />

        {/* Sync Existing button */}
        <Button
          size="sm"
          variant="outline"
          disabled={syncing || status.running}
          onClick={handleSync}
          className="h-6 px-2 text-[10px] font-mono gap-1"
          title="Push already-scraped job files to the tracker"
        >
          {syncing ? (
            <SpinnerGap size={10} className="animate-spin" />
          ) : (
            <DownloadSimple size={10} />
          )}
          Sync Existing
        </Button>

        {/* Source buttons */}
        <div className="flex items-center gap-1">
          <span className="text-[10px] font-mono text-steel mr-1">RUN:</span>
          {(['all', 'jobright', 'wellfound', 'ycjobs'] as ScraperSource[]).map((src) => (
            <Button
              key={src}
              size="sm"
              variant={src === 'all' ? 'default' : 'outline'}
              disabled={status.running}
              onClick={() => trigger(src)}
              className="h-6 px-2 text-[10px] font-mono"
            >
              {src === 'all' ? (
                <>
                  <Play size={10} className="mr-1" />
                  All
                </>
              ) : src === 'ycjobs' ? (
                'YC'
              ) : (
                src.charAt(0).toUpperCase() + src.slice(1)
              )}
            </Button>
          ))}
        </div>

        {/* Auto toggle */}
        <Button
          size="sm"
          variant={autoEnabled ? 'default' : 'outline'}
          onClick={toggleAuto}
          className="h-6 px-2 text-[10px] font-mono gap-1"
          title="Automatically scrape every 2 hours while this page is open"
        >
          <ArrowsClockwise size={10} />
          Auto 2h {autoEnabled ? 'ON' : 'OFF'}
        </Button>
      </div>

      {/* Pipeline config toggles — tailor after scrape, auto-apply after tailor, approval gate */}
      {pipelineConfig && (
        <div className="flex flex-wrap items-center gap-2 px-4 py-1.5 border-b border-ink bg-canvas">
          <span className="text-[10px] font-mono text-steel uppercase tracking-wide">
            Pipeline:
          </span>
          <Button
            size="sm"
            variant={pipelineConfig.auto_tailor ? 'default' : 'outline'}
            disabled={configSaving === 'auto_tailor'}
            onClick={() => void togglePipelineFlag('auto_tailor')}
            className="h-6 px-2 text-[10px] font-mono gap-1"
            title="After each scrape, automatically tailor all new JD-column cards with supported ATS"
          >
            <MagicWand size={10} />
            Auto-Tailor {pipelineConfig.auto_tailor ? 'ON' : 'OFF'}
          </Button>
          <Button
            size="sm"
            variant={pipelineConfig.auto_apply ? 'default' : 'outline'}
            disabled={configSaving === 'auto_apply'}
            onClick={() => void togglePipelineFlag('auto_apply')}
            className="h-6 px-2 text-[10px] font-mono gap-1"
            title="After each successful tailor, automatically submit to the ATS"
          >
            <PaperPlaneTilt size={10} />
            Auto-Apply {pipelineConfig.auto_apply ? 'ON' : 'OFF'}
          </Button>
          <Button
            size="sm"
            variant={pipelineConfig.approval_gate ? 'default' : 'outline'}
            disabled={configSaving === 'approval_gate'}
            onClick={() => void togglePipelineFlag('approval_gate')}
            className="h-6 px-2 text-[10px] font-mono gap-1"
            title="Require manual review before every ATS submission (recommended)"
          >
            <ShieldCheck size={10} />
            Approval Gate {pipelineConfig.approval_gate ? 'ON' : 'OFF'}
          </Button>
        </div>
      )}
    </>
  );
}
