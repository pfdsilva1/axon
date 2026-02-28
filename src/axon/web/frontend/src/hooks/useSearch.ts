import { useState, useEffect, useRef } from 'react';
import { searchApi } from '@/api/client';
import type { SearchResult } from '@/types';

/**
 * Debounced search hook that queries the Axon search API.
 *
 * Waits 200ms after the last keystroke before firing the request.
 * Returns an empty array when the query is blank or search is disabled.
 */
export function useSearch(query: string, enabled: boolean) {
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    if (!enabled || !query.trim()) {
      setResults([]);
      return;
    }

    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const data = await searchApi.search(query, 10);
        setResults(data.results);
      } catch {
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, 200);

    return () => clearTimeout(timerRef.current);
  }, [query, enabled]);

  return { results, loading };
}
