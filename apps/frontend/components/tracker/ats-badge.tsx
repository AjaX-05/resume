'use client';

import React from 'react';
import { CheckCircle, Warning, Question } from '@phosphor-icons/react';
import type { AtsInfo } from '@/lib/api/pipeline';

/**
 * Tiny colored pill shown on each application card indicating what ATS
 * powers its apply URL. Green = we can auto-apply. Orange = known but
 * unsupported (Workday/iCIMS). Gray = unknown / aggregator / no URL.
 */

const SUPPORTED_LABELS: Record<string, string> = {
  greenhouse: 'GH',
  ashby: 'ASHBY',
  lever: 'LEVER',
  workable: 'WRK',
  gem: 'GEM',
  dover: 'DOVER',
  rippling: 'RPL',
  smartrecruiters: 'SR',
  bamboohr: 'BHR',
  jobright: 'JR→',
  wellfound: 'WF→',
};

const UNSUPPORTED_LABELS: Record<string, string> = {
  workday: 'WD',
  icims: 'iCIMS',
  jobvite: 'JV',
  linkedin: 'LN',
  indeed: 'IND',
};

interface Props {
  info: AtsInfo | undefined;
}

export function AtsBadge({ info }: Props) {
  if (!info || !info.ats || info.ats === 'none') {
    return (
      <span
        className="inline-flex items-center gap-0.5 border border-steel bg-paper px-1 py-0.5 font-mono text-[9px] uppercase text-steel"
        title="No apply URL stored for this job"
      >
        <Question size={9} weight="bold" />
        no url
      </span>
    );
  }

  if (info.supported) {
    const label = SUPPORTED_LABELS[info.ats] ?? info.ats.toUpperCase().slice(0, 5);
    return (
      <span
        className="inline-flex items-center gap-0.5 border border-success bg-success-tint px-1 py-0.5 font-mono text-[9px] uppercase font-bold text-success"
        title={`Supported: auto-apply works via ${info.ats}. ${info.apply_url}`}
      >
        <CheckCircle size={9} weight="bold" />
        {label}
      </span>
    );
  }

  const label = UNSUPPORTED_LABELS[info.ats] ?? info.ats.toUpperCase().slice(0, 5);
  return (
    <span
      className="inline-flex items-center gap-0.5 border border-warning-text bg-warning-tint px-1 py-0.5 font-mono text-[9px] uppercase font-bold text-warning-text"
      title={`${info.ats} requires account/login — cannot auto-apply. ${info.apply_url}`}
    >
      <Warning size={9} weight="bold" />
      {label}
    </span>
  );
}
