import { useEffect } from 'react';
import { useViewStore } from '@/stores/viewStore';
import { useGraphStore } from '@/stores/graphStore';

/**
 * Global keyboard shortcut handler.
 *
 * Registers a single `keydown` listener on `window` and routes
 * modifier+key combos to the appropriate store actions.
 */
export function useKeyboard() {
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      const meta = e.metaKey || e.ctrlKey;

      // ---------------------------------------------------------------
      // Global shortcuts (work regardless of focus)
      // ---------------------------------------------------------------
      if (meta && e.key === 'k') {
        e.preventDefault();
        useViewStore.getState().toggleCommandPalette();
        return;
      }
      if (meta && e.key === '1') {
        e.preventDefault();
        useViewStore.getState().toggleLeftSidebar();
        return;
      }
      if (meta && e.key === '3') {
        e.preventDefault();
        useViewStore.getState().toggleRightPanel();
        return;
      }
      if (meta && e.key === '/') {
        e.preventDefault();
        useViewStore.getState().setActiveView('cypher');
        return;
      }
      if (meta && e.key === 'e') {
        e.preventDefault();
        useViewStore.getState().setActiveView('explorer');
        return;
      }
      if (meta && e.key === 'd') {
        e.preventDefault();
        useViewStore.getState().setActiveView('analysis');
        return;
      }
      if (e.key === 'Escape') {
        if (useViewStore.getState().commandPaletteOpen) {
          useViewStore.getState().setCommandPaletteOpen(false);
          return;
        }
        useGraphStore.getState().selectNode(null);
        return;
      }

      // ---------------------------------------------------------------
      // Graph shortcuts (only when focus is NOT inside an input/textarea)
      // ---------------------------------------------------------------
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement
      ) {
        return;
      }

      if (e.key === 'f') useGraphStore.getState().selectNode(null); // placeholder for fit-to-screen
      if (e.key === 'm') useGraphStore.getState().toggleMinimap();
      if (e.key === 'l') useGraphStore.getState().toggleHulls();
      if (e.key === '1') useViewStore.getState().setRightTab('context');
      if (e.key === '2') useViewStore.getState().setRightTab('impact');
      if (e.key === '3') useViewStore.getState().setRightTab('code');
      if (e.key === '4') useViewStore.getState().setRightTab('processes');
    }

    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);
}
