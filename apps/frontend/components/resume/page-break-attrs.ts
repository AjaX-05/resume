/**
 * Stable page-break hooks for every resume template.
 *
 * CSS-module class names are hashed at build time, so neither the print CSS
 * (`app/(default)/css/globals.css`) nor the builder preview's pagination
 * (`components/preview/page-breaks.ts`) can find entries and headings by class.
 * Templates spread these data attributes instead, and both read the same signal,
 * so the preview breaks pages where the PDF does.
 */

/** An entry with at least this many bullets may split between bullets.
 *  Set to 2 so almost every multi-bullet entry can split rather than being
 *  pushed whole to the next page (which caused large white-space blocks on
 *  page 1 — user feedback 2026-11-04). Only truly single-bullet entries
 *  stay atomic. */
export const SPLIT_ENTRY_MIN_BULLETS = 2;

/**
 * One entry (a job, project, degree, skill row or citation). It may split
 * between bullets when it has SPLIT_ENTRY_MIN_BULLETS or more, keeping its
 * header with the first bullet and the last bullets together. Single-bullet
 * entries stay atomic because there's nothing to split.
 */
export function resumeItem(bullets?: readonly unknown[] | null): { 'data-resume-item': string } {
  return {
    'data-resume-item': (bullets?.length ?? 0) >= SPLIT_ENTRY_MIN_BULLETS ? 'split' : '',
  };
}

/** A section heading: kept with the start of the content that follows it. */
export const RESUME_SECTION_TITLE = { 'data-resume-section-title': '' } as const;

/** A section: the heading and the entries it keeps company with. */
export const RESUME_SECTION = { 'data-resume-section': '' } as const;
