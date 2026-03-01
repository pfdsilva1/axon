/**
 * WebGL-rendered graph canvas powered by Sigma.js v3 and Graphology.
 *
 * Initialises the Sigma renderer when the Graphology instance is ready,
 * applies the active layout (force / tree / radial), and wires up
 * selection, hover, type-based filtering, and the minimap overlay
 * through the Zustand graph store.
 */

import { useEffect, useRef, useCallback, useState } from 'react';
import Sigma from 'sigma';
import type { MultiDirectedGraph } from 'graphology';
import { createNodeBorderProgram } from '@sigma/node-border';
import FA2LayoutSupervisor from 'graphology-layout-forceatlas2/worker';
import circular from 'graphology-layout/circular';
import circlePack from 'graphology-layout/circlepack';
import { useGraphStore } from '@/stores/graphStore';
import { useGraph } from '@/hooks/useGraph';
import { cn } from '@/lib/utils';
import { LoadingSpinner } from '@/components/shared/LoadingSpinner';
import { EmptyState } from '@/components/shared/EmptyState';
import { Minimap } from './Minimap';

interface GraphCanvasProps {
  className?: string;
}

// ---------------------------------------------------------------------------
// Layout algorithms
// ---------------------------------------------------------------------------

/** Position map: nodeId -> { x, y }. */
type PositionMap = Map<string, { x: number; y: number }>;

/**
 * Compute a hierarchical top-to-bottom tree layout.
 *
 * Uses **undirected BFS from the highest-degree hub node** so the graph
 * fans out into concentric layers of increasing width — layer 0 has 1
 * node, layer 1 has ~20, layer 2 has ~200, etc. This avoids the flat-line
 * bug caused by directed-edge filtering (which made ~80% of nodes roots
 * at layer 0).
 *
 * Disconnected nodes are placed on an extra bottom layer.
 * Within each layer, nodes are sorted by their `directory` attribute to
 * keep related symbols together.
 */
function computeTreeLayout(graph: MultiDirectedGraph): PositionMap {
  const positions: PositionMap = new Map();
  const nodeIds: string[] = [];
  graph.forEachNode((id) => nodeIds.push(id));

  if (nodeIds.length === 0) return positions;

  // Find the hub node: highest total degree.
  let hubNode = nodeIds[0];
  let maxDeg = 0;
  for (const id of nodeIds) {
    const deg = graph.degree(id);
    if (deg > maxDeg) {
      maxDeg = deg;
      hubNode = id;
    }
  }

  // Undirected BFS from the hub — every edge counts, regardless of type.
  const layers = new Map<string, number>();
  layers.set(hubNode, 0);
  const queue: string[] = [hubNode];

  while (queue.length > 0) {
    const current = queue.shift()!;
    const depth = layers.get(current)!;

    graph.forEachNeighbor(current, (neighbor) => {
      if (!layers.has(neighbor)) {
        layers.set(neighbor, depth + 1);
        queue.push(neighbor);
      }
    });
  }

  // Disconnected nodes → maxLayer + 1.
  const maxReachable = layers.size > 0 ? Math.max(...layers.values()) : 0;
  for (const id of nodeIds) {
    if (!layers.has(id)) {
      layers.set(id, maxReachable + 1);
    }
  }

  // Group nodes by layer, sorting within each layer by directory for cohesion.
  const layerGroups = new Map<number, string[]>();
  for (const [id, depth] of layers) {
    const group = layerGroups.get(depth) ?? [];
    group.push(id);
    layerGroups.set(depth, group);
  }

  for (const [, members] of layerGroups) {
    members.sort((a, b) => {
      const da = (graph.getNodeAttribute(a, 'directory') as string) ?? '';
      const db = (graph.getNodeAttribute(b, 'directory') as string) ?? '';
      return da.localeCompare(db);
    });
  }

  const maxLayer = Math.max(...layerGroups.keys());
  const LAYER_SPACING = 150;

  // Adaptive horizontal spacing based on the widest layer.
  const widestCount = Math.max(...[...layerGroups.values()].map((g) => g.length));
  const nodeSpacing = Math.max(30, Math.min(80, 2400 / widestCount));

  for (const [depth, members] of layerGroups) {
    const y = depth * LAYER_SPACING;
    const totalWidth = (members.length - 1) * nodeSpacing;
    const startX = -totalWidth / 2;

    for (let i = 0; i < members.length; i++) {
      positions.set(members[i], { x: startX + i * nodeSpacing, y });
    }
  }

  // Center around origin.
  if (maxLayer >= 0) {
    const centerY = (maxLayer * LAYER_SPACING) / 2;
    for (const [id, pos] of positions) {
      positions.set(id, { x: pos.x, y: pos.y - centerY });
    }
  }

  return positions;
}

/**
 * Compute a radial layout with adaptive ring sizing.
 *
 * When a `centerNodeId` is provided (the selected node), it becomes the
 * center — creating a true ego-graph view. Otherwise falls back to the
 * highest-degree node.
 *
 * Each ring's radius adapts to the number of nodes on it, so outer rings
 * with many nodes expand instead of packing tightly. Rings are offset by
 * half an arc-step to prevent radial stacking.
 */
function computeRadialLayout(graph: MultiDirectedGraph, centerNodeId?: string | null): PositionMap {
  const positions: PositionMap = new Map();
  const nodeIds: string[] = [];
  graph.forEachNode((id) => nodeIds.push(id));

  if (nodeIds.length === 0) return positions;

  // Choose center: selected node or highest-degree fallback.
  let centerNode: string;
  if (centerNodeId && graph.hasNode(centerNodeId)) {
    centerNode = centerNodeId;
  } else {
    centerNode = nodeIds[0];
    let maxDegree = 0;
    for (const id of nodeIds) {
      const deg = graph.degree(id);
      if (deg > maxDegree) {
        maxDegree = deg;
        centerNode = id;
      }
    }
  }

  // BFS from center to assign ring levels.
  const ringMap = new Map<string, number>();
  ringMap.set(centerNode, 0);
  const queue: string[] = [centerNode];

  while (queue.length > 0) {
    const current = queue.shift()!;
    const currentRing = ringMap.get(current)!;

    graph.forEachNeighbor(current, (neighbor) => {
      if (!ringMap.has(neighbor)) {
        ringMap.set(neighbor, currentRing + 1);
        queue.push(neighbor);
      }
    });
  }

  // Group by ring. Unreachable nodes go to a separate orphan ring.
  const ringGroups = new Map<number, string[]>();
  let maxRing = 0;
  for (const [id, ring] of ringMap) {
    const group = ringGroups.get(ring) ?? [];
    group.push(id);
    ringGroups.set(ring, group);
    if (ring > maxRing) maxRing = ring;
  }

  const orphans = nodeIds.filter((id) => !ringMap.has(id));
  if (orphans.length > 0) {
    ringGroups.set(maxRing + 1, orphans);
  }

  // Place center node at origin.
  positions.set(centerNode, { x: 0, y: 0 });

  // Adaptive radius: each ring expands based on how many nodes it has,
  // with a minimum gap of 100 from the previous ring.
  let prevRadius = 0;
  const sortedRings = [...ringGroups.keys()].filter((r) => r > 0).sort((a, b) => a - b);

  for (const ring of sortedRings) {
    const members = ringGroups.get(ring)!;
    const count = members.length;

    // Radius must be large enough to fit `count` nodes without overlap.
    // 80 units per node slot provides decent spacing at scale.
    const circumferenceNeeded = count * 80;
    const radiusFromCount = circumferenceNeeded / (2 * Math.PI);
    const radius = Math.max(prevRadius + 150, radiusFromCount);
    prevRadius = radius;

    // Offset each ring by half an arc-step to prevent radial stacking.
    const arcStep = (2 * Math.PI) / count;
    const ringOffset = (ring % 2) * (arcStep / 2);

    for (let i = 0; i < count; i++) {
      const angle = ringOffset + arcStep * i;
      positions.set(members[i], {
        x: radius * Math.cos(angle),
        y: radius * Math.sin(angle),
      });
    }
  }

  return positions;
}

/**
 * Animate node positions from their current locations to new target positions
 * over a given duration using requestAnimationFrame with ease-out cubic.
 *
 * Writes the current frame ID to `frameRef` on **every tick** so that
 * `cancelAnimationFrame(frameRef.current)` always cancels the latest
 * scheduled frame — fixing rapid layout-switch glitches.
 */
function animatePositions(
  graph: MultiDirectedGraph,
  targets: PositionMap,
  duration: number,
  frameRef: React.MutableRefObject<number>,
  onComplete?: () => void,
): void {
  // Capture starting positions.
  const starts: PositionMap = new Map();
  graph.forEachNode((id, attrs) => {
    starts.set(id, { x: attrs.x as number, y: attrs.y as number });
  });

  const t0 = performance.now();

  function tick() {
    const elapsed = performance.now() - t0;
    const progress = Math.min(elapsed / duration, 1);
    // Ease-out cubic for smooth deceleration.
    const ease = 1 - Math.pow(1 - progress, 3);

    graph.forEachNode((id) => {
      const start = starts.get(id);
      const target = targets.get(id);
      if (!start || !target) return;

      graph.setNodeAttribute(id, 'x', start.x + (target.x - start.x) * ease);
      graph.setNodeAttribute(id, 'y', start.y + (target.y - start.y) * ease);
    });

    if (progress < 1) {
      frameRef.current = requestAnimationFrame(tick);
    } else {
      onComplete?.();
    }
  }

  frameRef.current = requestAnimationFrame(tick);
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

/**
 * Core graph visualisation component.
 *
 * Renders the knowledge graph using Sigma.js with WebGL acceleration.
 * Supports three layout modes: force (ForceAtlas2 in web worker),
 * tree (hierarchical top-to-bottom), and radial (concentric rings).
 * Node/edge visibility, selection, and hover dimming are all handled
 * through nodeReducer/edgeReducer callbacks.
 */
export function GraphCanvas({ className }: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const layoutRef = useRef<FA2LayoutSupervisor | null>(null);
  const animFrameRef = useRef<number>(0);
  const { graphRef, loading, error } = useGraph();
  const [layoutRunning, setLayoutRunning] = useState(false);
  // Local state to trigger re-render when sigma instance becomes available.
  const [sigmaReady, setSigmaReady] = useState(false);

  const selectedNodeId = useGraphStore((s) => s.selectedNodeId);
  const hoveredNodeId = useGraphStore((s) => s.hoveredNodeId);
  const highlightedNodeIds = useGraphStore((s) => s.highlightedNodeIds);
  const visibleNodeTypes = useGraphStore((s) => s.visibleNodeTypes);
  const visibleEdgeTypes = useGraphStore((s) => s.visibleEdgeTypes);
  const selectNode = useGraphStore((s) => s.selectNode);
  const setHoveredNode = useGraphStore((s) => s.setHoveredNode);
  const layoutMode = useGraphStore((s) => s.layoutMode);
  const minimapVisible = useGraphStore((s) => s.minimapVisible);

  /** Zoom the camera in by one step. */
  const zoomIn = useCallback(() => {
    const camera = sigmaRef.current?.getCamera();
    if (camera) {
      camera.animatedZoom({ duration: 200 });
    }
  }, []);

  /** Zoom the camera out by one step. */
  const zoomOut = useCallback(() => {
    const camera = sigmaRef.current?.getCamera();
    if (camera) {
      camera.animatedUnzoom({ duration: 200 });
    }
  }, []);

  /** Reset the camera to show the entire graph. */
  const fitToScreen = useCallback(() => {
    const camera = sigmaRef.current?.getCamera();
    if (camera) {
      camera.animatedReset({ duration: 300 });
    }
  }, []);

  /** Toggle the ForceAtlas2 layout on/off (only relevant in force mode). */
  const toggleLayout = useCallback(() => {
    const graph = graphRef.current;
    const layout = layoutRef.current;
    if (!layout || !graph) return;

    if (layout.isRunning()) {
      layout.stop();
      setLayoutRunning(false);
    } else {
      // Kill the stale supervisor and create a fresh one with weaker settings.
      // This resets FA2's internal speed accumulator, preventing vibration on
      // resume after the layout has already converged.
      layout.kill();
      const fresh = new FA2LayoutSupervisor(graph, {
        settings: {
          gravity: 0.5,
          scalingRatio: 5,
          strongGravityMode: false,
          linLogMode: false,
          outboundAttractionDistribution: true,
          barnesHutOptimize: true,
          barnesHutTheta: 0.5,
          slowDown: 15,
        },
      });
      fresh.start();
      layoutRef.current = fresh;
      setLayoutRunning(true);

      // Auto-stop after 6s.
      setTimeout(() => {
        if (fresh.isRunning()) {
          fresh.stop();
          setLayoutRunning(false);
        }
      }, 6_000);
    }
  }, []);

  // Initialise Sigma and ForceAtlas2 when the Graphology graph is ready.
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !graphRef.current) return;
    const graph = graphRef.current;

    // Snapshot the current store values for the reducers. The refresh effect
    // below triggers sigma.refresh() whenever these change, which causes
    // Sigma to re-invoke the reducers with fresh closure values.
    // Create a bordered node program: inner fill + thin outer border ring.
    const BorderedNodeProgram = createNodeBorderProgram({
      borders: [
        { size: { value: 0.15, mode: 'relative' }, color: { attribute: 'borderColor', defaultValue: '#5a6a7a' } },
        { size: { fill: true }, color: { attribute: 'color' } },
      ],
    });

    const sigma = new Sigma(graph, container, {
      renderLabels: true,
      labelFont: 'JetBrains Mono, monospace',
      labelSize: 11,
      labelWeight: '500',
      labelColor: { color: '#8899aa' },
      defaultEdgeColor: '#2a3a4d',
      defaultNodeColor: '#4a5a6a',
      // Only show labels for nodes that are large enough (high degree).
      labelRenderedSizeThreshold: 12,
      // Reduce label density to prevent overlap.
      labelDensity: 0.5,
      labelGridCellSize: 120,
      // Hide edges while panning/zooming to reduce clutter.
      hideEdgesOnMove: true,
      // Use bordered node program instead of plain circle.
      defaultNodeType: 'bordered',
      nodeProgramClasses: {
        bordered: BorderedNodeProgram,
      },

      nodeReducer: (node, data) => {
        const res = { ...data };
        const nodeType = (data.nodeType ?? '') as string;
        const state = useGraphStore.getState();

        if (!state.visibleNodeTypes.has(nodeType)) {
          res.hidden = true;
          return res;
        }

        // Set-based highlighting (file/folder/community/dead code).
        if (state.highlightedNodeIds.size > 0) {
          if (state.highlightedNodeIds.has(node)) {
            res.size = (res.size ?? 3) * 1.3;
            res.zIndex = 2;
          } else {
            res.color = '#141a22';
            res.borderColor = '#141a22';
            res.label = '';
            res.zIndex = 0;
          }
          if (state.hoveredNodeId && node === state.hoveredNodeId) {
            res.highlighted = true;
            res.forceLabel = true;
          }
          return res;
        }

        // Single-node selection dimming.
        if (state.selectedNodeId && node !== state.selectedNodeId) {
          const isNeighbor =
            graph.hasEdge(state.selectedNodeId, node) ||
            graph.hasEdge(node, state.selectedNodeId);
          if (!isNeighbor) {
            res.color = '#141a22';
            res.borderColor = '#141a22';
            res.label = '';
            res.zIndex = 0;
          } else {
            res.forceLabel = true;
            res.zIndex = 2;
          }
        }

        if (state.selectedNodeId && node === state.selectedNodeId) {
          res.highlighted = true;
          res.forceLabel = true;
          res.zIndex = 3;
        }

        if (state.hoveredNodeId && node === state.hoveredNodeId) {
          res.highlighted = true;
          res.forceLabel = true;
        }

        return res;
      },

      edgeReducer: (edge, data) => {
        const res = { ...data };
        const edgeType = (data.edgeType ?? '') as string;
        const state = useGraphStore.getState();

        if (!state.visibleEdgeTypes.has(edgeType)) {
          res.hidden = true;
          return res;
        }

        // Set-based highlighting: show edges between highlighted nodes.
        if (state.highlightedNodeIds.size > 0) {
          const source = graph.source(edge);
          const target = graph.target(edge);
          if (state.highlightedNodeIds.has(source) && state.highlightedNodeIds.has(target)) {
            res.color = '#4488cc';
            res.size = 1.2;
          } else {
            res.hidden = true;
          }
          return res;
        }

        // Single-node selection.
        if (state.selectedNodeId) {
          const source = graph.source(edge);
          const target = graph.target(edge);
          if (source !== state.selectedNodeId && target !== state.selectedNodeId) {
            res.hidden = true;
          } else {
            res.color = '#4488cc';
            res.size = 1.5;
          }
        }

        return res;
      },
    });

    sigmaRef.current = sigma;
    setSigmaReady(true);

    // ---------------------------------------------------------------
    // Interaction events + node dragging
    // ---------------------------------------------------------------
    let draggedNode: string | null = null;
    let isDragging = false;
    let dragStartX = 0;
    let dragStartY = 0;
    const DRAG_THRESHOLD = 5;

    sigma.on('downNode', (e) => {
      isDragging = true;
      draggedNode = e.node;
      graph.setNodeAttribute(draggedNode, 'fixed', true);
      if (layoutRef.current?.isRunning()) layoutRef.current.stop();
      sigma.getCamera().disable();
    });

    sigma.getMouseCaptor().on('mousemovebody', (e) => {
      if (!isDragging || !draggedNode) return;
      const pos = sigma.viewportToGraph(e);
      graph.setNodeAttribute(draggedNode, 'x', pos.x);
      graph.setNodeAttribute(draggedNode, 'y', pos.y);
      e.preventSigmaDefault();
      e.original.preventDefault();
      e.original.stopPropagation();
    });

    sigma.getMouseCaptor().on('mousedown', (e) => {
      dragStartX = e.x;
      dragStartY = e.y;
    });

    sigma.getMouseCaptor().on('mouseup', (e) => {
      if (draggedNode) {
        const dx = Math.abs(e.x - dragStartX);
        const dy = Math.abs(e.y - dragStartY);
        if (dx < DRAG_THRESHOLD && dy < DRAG_THRESHOLD) {
          selectNode(draggedNode);
        }
        graph.removeNodeAttribute(draggedNode, 'fixed');
      }
      isDragging = false;
      draggedNode = null;
      sigma.getCamera().enable();
    });

    sigma.on('clickStage', () => {
      selectNode(null);
      useGraphStore.getState().setHighlightedNodes(new Set());
    });

    sigma.on('enterNode', ({ node }) => {
      setHoveredNode(node);
      container.style.cursor = 'grab';
    });

    sigma.on('leaveNode', () => {
      setHoveredNode(null);
      container.style.cursor = 'default';
    });

    // ---------------------------------------------------------------
    // ForceAtlas2 layout
    // ---------------------------------------------------------------
    const layout = new FA2LayoutSupervisor(graph, {
      settings: {
        gravity: 1,
        scalingRatio: 5,
        strongGravityMode: true,
        linLogMode: false,
        outboundAttractionDistribution: true,
        barnesHutOptimize: true,
        barnesHutTheta: 0.5,
        slowDown: 10,
      },
    });
    layout.start();
    layoutRef.current = layout;
    setLayoutRunning(true);

    const timer = setTimeout(() => {
      if (layout.isRunning()) {
        layout.stop();
        setLayoutRunning(false);
      }
    }, 6_000);

    return () => {
      clearTimeout(timer);
      cancelAnimationFrame(animFrameRef.current);
      layout.kill();
      sigma.kill();
      sigmaRef.current = null;
      layoutRef.current = null;
      setSigmaReady(false);
      setLayoutRunning(false);
    };
  }, [loading, selectNode, setHoveredNode]); // eslint-disable-line react-hooks/exhaustive-deps

  // Apply layout mode changes.
  useEffect(() => {
    const graph = graphRef.current;
    const layout = layoutRef.current;
    if (!graph || !sigmaRef.current) return;

    cancelAnimationFrame(animFrameRef.current);

    if (layoutMode === 'force') {
      if (layout && !layout.isRunning()) {
        layout.start();
        setLayoutRunning(true);

        const timer = setTimeout(() => {
          if (layout.isRunning()) {
            layout.stop();
            setLayoutRunning(false);
          }
        }, 6_000);

        return () => clearTimeout(timer);
      }
    } else {
      if (layout && layout.isRunning()) {
        layout.stop();
        setLayoutRunning(false);
      }

      let targets: PositionMap;

      if (layoutMode === 'tree') {
        targets = computeTreeLayout(graph);
      } else if (layoutMode === 'radial') {
        targets = computeRadialLayout(graph, selectedNodeId);
      } else if (layoutMode === 'community') {
        // Use circlePack to group nodes by community attribute.
        // Assign community from store data, falling back to directory.
        const communities = useGraphStore.getState().communities;
        const memberToCommunity = new Map<string, string>();
        for (const c of communities) {
          for (const memberId of c.members) {
            memberToCommunity.set(memberId, c.id);
          }
        }

        graph.forEachNode((id, attrs) => {
          const communityId = memberToCommunity.get(id) ?? (attrs.directory as string) ?? 'unknown';
          graph.setNodeAttribute(id, 'community', communityId);
        });

        circlePack.assign(graph, { hierarchyAttributes: ['community'], scale: 1000 });

        // Read assigned positions into our PositionMap.
        targets = new Map();
        graph.forEachNode((id, attrs) => {
          targets.set(id, { x: attrs.x as number, y: attrs.y as number });
        });
      } else {
        // 'circular' layout — all nodes on a ring.
        // Scale adapts to node count so nodes don't bunch up.
        const nodeCount = graph.order;
        const circularScale = Math.max(500, nodeCount * 2);
        circular.assign(graph, { scale: circularScale });

        targets = new Map();
        graph.forEachNode((id, attrs) => {
          targets.set(id, { x: attrs.x as number, y: attrs.y as number });
        });
      }

      animatePositions(graph, targets, 500, animFrameRef);
    }
  }, [layoutMode, selectedNodeId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Re-render Sigma when filters, selection, or hover state change.
  useEffect(() => {
    sigmaRef.current?.refresh();
  }, [selectedNodeId, hoveredNodeId, highlightedNodeIds, visibleNodeTypes, visibleEdgeTypes]);

  // Determine if the graph is empty (loaded but no nodes).
  const nodes = useGraphStore((s) => s.nodes);
  const graphEmpty = !loading && !error && nodes.length === 0;

  if (error) {
    return (
      <div
        className={cn('flex items-center justify-center h-full', className)}
        style={{
          color: 'var(--danger)',
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 12,
        }}
      >
        Failed to load graph: {error}
      </div>
    );
  }

  if (loading) {
    return (
      <div className={cn('flex items-center justify-center h-full', className)}>
        <LoadingSpinner message="Loading graph..." />
      </div>
    );
  }

  if (graphEmpty) {
    return (
      <div className={cn('flex items-center justify-center h-full', className)}>
        <EmptyState message="No graph data. Run `axon index` first." />
      </div>
    );
  }

  return (
    <div className={cn('relative w-full h-full', className)} style={{ background: 'var(--bg-primary)' }}>
      <div ref={containerRef} className="w-full h-full" />
      <GraphControls
        onZoomIn={zoomIn}
        onZoomOut={zoomOut}
        onFitToScreen={fitToScreen}
        onToggleLayout={toggleLayout}
        layoutRunning={layoutRunning}
      />
      {layoutRunning && <LayoutIndicator />}
      {minimapVisible && sigmaReady && sigmaRef.current && (
        <Minimap sigma={sigmaRef.current} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline GraphControls (small enough to co-locate)
// ---------------------------------------------------------------------------

interface GraphControlsProps {
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFitToScreen: () => void;
  onToggleLayout: () => void;
  layoutRunning: boolean;
}

/**
 * Floating control bar at the bottom-left of the graph canvas.
 *
 * Four small buttons stacked vertically: zoom in, zoom out, fit to screen,
 * and play/pause the force-directed layout.
 */
function GraphControls({
  onZoomIn,
  onZoomOut,
  onFitToScreen,
  onToggleLayout,
  layoutRunning,
}: GraphControlsProps) {
  return (
    <div
      className="absolute bottom-3 left-3 flex flex-col gap-1"
      style={{ zIndex: 10 }}
    >
      <ControlButton onClick={onZoomIn} title="Zoom in" aria-label="Zoom in">
        <PlusIcon />
      </ControlButton>
      <ControlButton onClick={onZoomOut} title="Zoom out" aria-label="Zoom out">
        <MinusIcon />
      </ControlButton>
      <ControlButton onClick={onFitToScreen} title="Fit to screen" aria-label="Fit to screen">
        <MaximizeIcon />
      </ControlButton>
      <ControlButton onClick={onToggleLayout} title={layoutRunning ? 'Pause layout' : 'Resume layout'} aria-label={layoutRunning ? 'Pause layout' : 'Resume layout'}>
        {layoutRunning ? <PauseIcon /> : <PlayIcon />}
      </ControlButton>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Control button wrapper
// ---------------------------------------------------------------------------

interface ControlButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  children: React.ReactNode;
}

function ControlButton({ children, ...props }: ControlButtonProps) {
  return (
    <button
      type="button"
      className="flex items-center justify-center transition-colors"
      style={{
        width: 24,
        height: 24,
        background: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderRadius: 2,
        color: 'var(--text-secondary)',
        cursor: 'pointer',
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.color = 'var(--accent)';
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-secondary)';
      }}
      {...props}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Inline SVG icons (12x12) to avoid importing lucide-react for 4 tiny icons
// ---------------------------------------------------------------------------

function PlusIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
      <line x1="6" y1="2" x2="6" y2="10" />
      <line x1="2" y1="6" x2="10" y2="6" />
    </svg>
  );
}

function MinusIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
      <line x1="2" y1="6" x2="10" y2="6" />
    </svg>
  );
}

function MaximizeIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
      <rect x="2" y="2" width="8" height="8" rx="0.5" />
      <line x1="4" y1="4" x2="4" y2="4.01" strokeLinecap="round" />
    </svg>
  );
}

function PlayIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" stroke="none">
      <polygon points="3,1.5 10,6 3,10.5" />
    </svg>
  );
}

function PauseIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" stroke="none">
      <rect x="2.5" y="2" width="2.5" height="8" rx="0.5" />
      <rect x="7" y="2" width="2.5" height="8" rx="0.5" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Layout-running indicator (bottom-left, above controls)
// ---------------------------------------------------------------------------

/**
 * Small indicator shown while ForceAtlas2 is optimising the layout.
 */
function LayoutIndicator() {
  return (
    <div
      style={{
        position: 'absolute',
        bottom: 120,
        left: 12,
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        background: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderRadius: 2,
        padding: '3px 8px',
        zIndex: 10,
      }}
    >
      <span
        style={{
          display: 'inline-block',
          width: 6,
          height: 6,
          borderRadius: '50%',
          background: 'var(--accent)',
          animation: 'axon-pulse 1.4s ease-in-out infinite',
        }}
      />
      <span
        style={{
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 10,
          color: 'var(--text-secondary)',
        }}
      >
        Optimizing layout...
      </span>
      <style>{`
        @keyframes axon-pulse {
          0%, 100% { transform: scale(0.8); opacity: 0.5; }
          50%      { transform: scale(1.2); opacity: 1; }
        }
      `}</style>
    </div>
  );
}
