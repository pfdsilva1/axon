/**
 * Server-Sent Events hook for live graph reload.
 *
 * Opens an EventSource connection to `/api/events` and listens for reindex
 * lifecycle events.  When a `reindex_complete` event fires, the hook fetches
 * fresh graph data from the API and pushes it into the Zustand store, causing
 * the Sigma canvas to re-render with updated data.
 *
 * Reconnects automatically after a 5-second delay on connection errors.
 */

import { useEffect, useRef } from 'react';
import { graphApi } from '@/api/client';
import { useGraphStore } from '@/stores/graphStore';

/**
 * Subscribe to backend SSE events and auto-refresh graph data on reindex.
 *
 * Should be called once at the application root (e.g. inside `<App />`).
 */
export function useSSE(): void {
  const sourceRef = useRef<EventSource | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const setGraphData = useGraphStore((s) => s.setGraphData);

  useEffect(() => {
    function connect(): void {
      const source = new EventSource('/api/events');
      sourceRef.current = source;

      source.addEventListener('reindex_complete', (e: MessageEvent) => {
        try {
          // Parse the event payload (contains added/removed/modified node IDs).
          const _data = JSON.parse(e.data as string) as {
            added?: string[];
            removed?: string[];
            modified?: string[];
          };

          // Fetch the full updated graph and push it into the store.
          graphApi
            .getGraph()
            .then((graphData) => {
              setGraphData(graphData.nodes, graphData.edges);
            })
            .catch((err: unknown) => {
              console.error('[SSE] Failed to fetch updated graph:', err);
            });
        } catch (err) {
          console.error('[SSE] Failed to parse reindex_complete event:', err);
        }
      });

      source.addEventListener('reindex_start', () => {
        // Informational -- could trigger a loading spinner in the future.
        console.info('[SSE] Reindex started');
      });

      source.onerror = () => {
        // Close the broken connection and schedule a reconnect.
        source.close();
        sourceRef.current = null;

        reconnectTimerRef.current = setTimeout(() => {
          reconnectTimerRef.current = null;
          connect();
        }, 5_000);
      };
    }

    connect();

    return () => {
      sourceRef.current?.close();
      sourceRef.current = null;

      if (reconnectTimerRef.current !== null) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };
  }, [setGraphData]);
}
