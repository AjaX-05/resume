import { describe, expect, it } from 'vitest';
import {
  cardSeniority,
  formatPostedAgo,
  latestScrapeBatch,
  parseScrapeMeta,
  postedWithinMinutes,
} from '@/lib/scrape-meta';

const notes = `Source: jobright | https://jobright.ai/jobs/info/abc
Salary: $186K/yr - $219K/yr
Posted: 2026-11-08T17:08:00Z
Seniority: Senior
Scrape-batch: 2026-11-08T18:37:21Z
Lookback: 3h
Discovered: 2026-11-08T18:37:32Z`;

describe('parseScrapeMeta', () => {
  it('reads posted time, seniority, and scrape batch', () => {
    expect(parseScrapeMeta(notes)).toMatchObject({
      postedAt: '2026-11-08T17:08:00Z',
      seniority: 'Senior',
      scrapeBatch: '2026-11-08T18:37:21Z',
    });
  });

  it('picks the latest scrape batch for this-pull highlighting', () => {
    expect(
      latestScrapeBatch([
        'Scrape-batch: 2026-11-08T18:00:00Z',
        notes,
        'Scrape-batch: 2026-11-08T18:10:00Z',
      ])
    ).toBe('2026-11-08T18:37:21Z');
  });

  it('formats a relative posted label', () => {
    const iso = new Date(Date.now() - 12 * 60 * 1000).toISOString();
    expect(formatPostedAgo(iso)).toBe('Posted 12m ago');
  });

  it('keeps unknown posted times when filtering', () => {
    expect(postedWithinMinutes(null, 30)).toBe(true);
  });

  it('prefers notes seniority over the title', () => {
    expect(cardSeniority('Junior Engineer', notes)).toBe('Senior');
  });

  it('classifies from the title when notes have no seniority', () => {
    expect(cardSeniority('Staff Software Engineer', null)).toBe('Staff');
    expect(cardSeniority('Software Engineer', null)).toBe('Mid');
    expect(cardSeniority('Junior Developer', null)).toBe('Entry');
  });
});
