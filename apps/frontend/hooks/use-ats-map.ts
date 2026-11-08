'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { detectAts, type AtsMap } from '@/lib/api/pipeline';

/**
 * Fetch and cache ATS classification for a list of application IDs.
 *
 * Debounces calls — if the ID list changes while a fetch is in flight,
 * the newest list wins. Cached results persist across re-renders so the
 * Kanban board doesn't re-call /pipeline/ats-detect for cards it already
 * knows about.
 */
export function useAtsMap(applicationIds: string[]): {
  atsMap: AtsMap;
  loading: boolean;
  refresh: () => void;
} {
  const [atsMap, setAtsMap] = useState<AtsMap>({});
  const [loading, setLoading] = useState(false);
  const inFlightRef = useRef(false);

  const fetchMissing = useCallback(async (ids: string[]) => {
    if (ids.length === 0) return;
    inFlightRef.current = true;
    setLoading(true);
    try {
      const result = await detectAts(ids);
      setAtsMap((prev) => ({ ...prev, ...result }));
    } catch {
      /* silent — badge just won't appear */
    } finally {
      inFlightRef.current = false;
      setLoading(false);
    }
  }, []);

  // On every id-list change, fetch any ids we don't yet know
  useEffect(() => {
    const missing = applicationIds.filter((id) => !(id in atsMap));
    if (missing.length === 0) return;
    void fetchMissing(missing);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applicationIds.join(',')]);

  const refresh = useCallback(() => {
    setAtsMap({});
    void fetchMissing(applicationIds);
  }, [fetchMissing, applicationIds]);

  return { atsMap, loading, refresh };
}
