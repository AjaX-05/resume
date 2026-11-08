import { describe, expect, it } from 'vitest';
import { cardFingerprint, pickUniqueTailorIds } from '@/lib/card-fingerprint';
import type { Application } from '@/lib/api/tracker';

function card(
  id: string,
  company: string,
  role: string,
  created_at = '2026-11-08T12:00:00Z'
): Application {
  return {
    application_id: id,
    job_id: `job-${id}`,
    resume_id: 'master',
    master_resume_id: 'master',
    status: 'saved',
    company,
    role,
    applied_at: null,
    notes: 'Apply: https://jobright.ai/jobs/info/abc',
    position: 0,
    created_at,
    updated_at: created_at,
  };
}

describe('cardFingerprint', () => {
  it('treats Role @ Company as the same job as Role at Company', () => {
    expect(cardFingerprint('Omnicom', 'Senior AI Engineer @ Omnicom')).toBe(
      cardFingerprint('Omnicom', 'Senior AI Engineer')
    );
  });

  it('does not collapse empty cards', () => {
    expect(cardFingerprint('', '')).toBe('');
  });
});

describe('pickUniqueTailorIds', () => {
  const ats = {
    a: { ats: 'jobright', supported: true, apply_url: 'https://jobright.ai/a' },
    b: { ats: 'jobright', supported: true, apply_url: 'https://jobright.ai/b' },
    c: { ats: 'none', supported: false, apply_url: '' },
  };

  it('keeps one of two Omnicom twins and skips the already-tailored copy', () => {
    const selected = [
      card('a', 'Omnicom', 'Senior AI Engineer @ Omnicom', '2026-11-08T10:00:00Z'),
      card('b', 'Omnicom', 'Senior AI Engineer', '2026-11-08T12:00:00Z'),
    ];
    const result = pickUniqueTailorIds(selected, [], ats);
    expect(result.ids).toEqual(['b']);
    expect(result.skippedDup).toBe(1);
  });

  it('skips a JD that already has a tailored twin', () => {
    const selected = [card('b', 'Omnicom', 'Senior AI Engineer')];
    const tailored = [
      { ...card('old', 'Omnicom', 'Senior AI Engineer @ Omnicom'), status: 'applied' as const },
    ];
    const result = pickUniqueTailorIds(selected, tailored, ats);
    expect(result.ids).toEqual([]);
    expect(result.skippedDup).toBe(1);
  });
});
