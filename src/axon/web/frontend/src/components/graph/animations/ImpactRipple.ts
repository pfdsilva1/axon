/**
 * Impact ripple animation for visualising blast-radius analysis on a Sigma graph.
 *
 * Applies a depth-based color cascade: the target node glows white, immediate
 * callers flash red, depth-2 nodes turn orange after 300ms, and depth-3+ nodes
 * turn yellow after 600ms.  All other nodes and edges are dimmed to near-black
 * so the blast path stands out.
 *
 * Returns a cleanup function that restores the original Sigma reducer settings.
 */

import type Sigma from 'sigma';
import type { Attributes } from 'graphology-types';
import type { NodeDisplayData, EdgeDisplayData } from 'sigma/types';

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

export interface ImpactConfig {
  /** The node whose blast radius is being visualised. */
  targetNodeId: string;
  /** Map from nodeId to depth level (1 = immediate, 2, 3+). */
  depthMap: Map<string, number>;
}

/** Colors mapped to depth levels -- matches the CSS variable palette. */
const DEPTH_COLORS: Record<number, string> = {
  1: '#f85149', // --danger  (red)
  2: '#f0883e', // --orange
  3: '#d4a72c', // --yellow
};

const TARGET_COLOR = '#ffffff';
const DIM_NODE_COLOR = '#0a0e14';
const DIM_EDGE_COLOR = '#0a0e14';
const BLAST_EDGE_COLOR = '#f85149';

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Apply the impact ripple animation to a Sigma instance.
 *
 * Nodes in the blast radius are highlighted by depth with a staggered reveal
 * (300ms between each depth tier).  All other nodes and edges are dimmed.
 *
 * @returns A cleanup function that removes the animation and refreshes Sigma.
 */
export function applyImpactRipple(
  sigma: Sigma,
  config: ImpactConfig,
): () => void {
  const { targetNodeId, depthMap } = config;
  const graph = sigma.getGraph();

  // Track which depth tiers have been revealed so far.
  const revealedDepths = new Set<number>();
  revealedDepths.add(1); // Depth 1 shows immediately.

  // Build a set of all edges that lie on the blast path (connecting nodes in
  // the depth map).
  const blastEdges = new Set<string>();
  graph.forEachEdge(
    (edge: string, _attrs: Attributes, source: string, target: string) => {
      const sourceInBlast = source === targetNodeId || depthMap.has(source);
      const targetInBlast = target === targetNodeId || depthMap.has(target);
      if (sourceInBlast && targetInBlast) {
        blastEdges.add(edge);
      }
    },
  );

  // Save the original reducers so we can restore them on cleanup.
  const originalNodeReducer = sigma.getSetting('nodeReducer');
  const originalEdgeReducer = sigma.getSetting('edgeReducer');

  // -- Node reducer --------------------------------------------------------

  const nodeReducer = (
    node: string,
    data: Attributes,
  ): Partial<NodeDisplayData> => {
    const res = { ...data } as Partial<NodeDisplayData>;

    if (node === targetNodeId) {
      res.color = TARGET_COLOR;
      res.size = (data.size as number ?? 4) * 2;
      res.highlighted = true;
      res.zIndex = 10;
      return res;
    }

    const depth = depthMap.get(node);
    if (depth !== undefined) {
      // Clamp depth to 3 for color lookup.
      const colorKey = Math.min(depth, 3);

      // Only show this tier if it has been revealed by the staggered timeout.
      if (!revealedDepths.has(colorKey)) {
        res.color = DIM_NODE_COLOR;
        res.label = null;
        return res;
      }

      res.color = DEPTH_COLORS[colorKey] ?? DEPTH_COLORS[3];
      const sizeMultiplier =
        colorKey === 1 ? 1.5 : colorKey === 2 ? 1.3 : 1.1;
      res.size = (data.size as number ?? 4) * sizeMultiplier;
      res.highlighted = true;
      res.zIndex = 5;
      return res;
    }

    // Not part of the blast radius -- dim it.
    res.color = DIM_NODE_COLOR;
    res.label = null;
    return res;
  };

  // -- Edge reducer --------------------------------------------------------

  const edgeReducer = (
    edge: string,
    data: Attributes,
  ): Partial<EdgeDisplayData> => {
    const res = { ...data } as Partial<EdgeDisplayData>;

    if (blastEdges.has(edge)) {
      res.color = BLAST_EDGE_COLOR;
      res.size = 2;
      return res;
    }

    res.color = DIM_EDGE_COLOR;
    return res;
  };

  // Apply the reducers.
  sigma.setSetting('nodeReducer', nodeReducer);
  sigma.setSetting('edgeReducer', edgeReducer);
  sigma.refresh();

  // -- Staggered depth reveal ---------------------------------------------

  const timers: ReturnType<typeof setTimeout>[] = [];

  // Reveal depth 2 after 300ms.
  timers.push(
    setTimeout(() => {
      revealedDepths.add(2);
      sigma.refresh();
    }, 300),
  );

  // Reveal depth 3+ after 600ms.
  timers.push(
    setTimeout(() => {
      revealedDepths.add(3);
      sigma.refresh();
    }, 600),
  );

  // -- Cleanup function ---------------------------------------------------

  return () => {
    for (const timer of timers) {
      clearTimeout(timer);
    }
    sigma.setSetting('nodeReducer', originalNodeReducer);
    sigma.setSetting('edgeReducer', originalEdgeReducer);
    sigma.refresh();
  };
}
