import type { Application } from '@/lib/api/tracker';
import type { AtsMap } from '@/lib/api/pipeline';

/** Company + role identity that treats "Role @ Company" as the same job as "Role". */
export function cardFingerprint(
  company?: string | null,
  role?: string | null
): string {
  let companyKey = (company || '').trim().toLowerCase();
  let roleKey = (role || '').trim().replace(/\s+/g, ' ').toLowerCase();
  if (companyKey) {
    for (const sep of [' @ ', ' at ']) {
      const suffix = `${sep}${companyKey}`;
      if (roleKey.endsWith(suffix)) {
        roleKey = roleKey.slice(0, -suffix.length).trim();
        break;
      }
    }
  } else if (roleKey) {
    for (const sep of [' @ ', ' at ']) {
      const idx = roleKey.lastIndexOf(sep);
      if (idx > 0) {
        companyKey = roleKey.slice(idx + sep.length).trim();
        roleKey = roleKey.slice(0, idx).trim();
        break;
      }
    }
  }
  if (!companyKey && !roleKey) return '';
  return `${companyKey}::${roleKey}`;
}

/**
 * One card per company+role. Prefer a supported apply URL, then the newest card.
 * Drops JDs that already have a tailored twin so bulk tailor doesn't burn the LLM twice.
 */
export function pickUniqueTailorIds(
  selected: Application[],
  alreadyTailored: Application[],
  atsMap: AtsMap
): { ids: string[]; skippedDup: number } {
  const taken = new Set(
    alreadyTailored
      .map((card) => cardFingerprint(card.company, card.role))
      .filter(Boolean)
  );
  const sorted = [...selected].sort((a, b) => {
    const aOk = atsMap[a.application_id]?.supported ? 0 : 1;
    const bOk = atsMap[b.application_id]?.supported ? 0 : 1;
    if (aOk !== bOk) return aOk - bOk;
    return (b.created_at || '').localeCompare(a.created_at || '');
  });
  const ids: string[] = [];
  let skippedDup = 0;
  for (const card of sorted) {
    if (!atsMap[card.application_id]?.supported) continue;
    const fingerprint = cardFingerprint(card.company, card.role);
    if (fingerprint && taken.has(fingerprint)) {
      skippedDup += 1;
      continue;
    }
    if (fingerprint) taken.add(fingerprint);
    ids.push(card.application_id);
  }
  return { ids, skippedDup };
}
