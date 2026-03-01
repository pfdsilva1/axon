/**
 * Converts raw API graph data into a Graphology instance for Sigma.js rendering.
 *
 * Assigns visual attributes (color, size, position) to each node and edge so
 * that Sigma can render the graph without additional processing.
 */

import { MultiDirectedGraph } from 'graphology';
import type { GraphNode, GraphEdge, NodeLabel } from '@/types';

const NODE_COLORS: Record<string, { fill: string; border: string }> = {
  function:   { fill: '#61AFEF', border: '#7EC4FF' },
  method:     { fill: '#56B6C2', border: '#73CAD4' },
  class:      { fill: '#E5C07B', border: '#F0D192' },
  interface:  { fill: '#C678DD', border: '#D899EA' },
  type_alias: { fill: '#98C379', border: '#B0D494' },
  enum:       { fill: '#E06C75', border: '#E98B93' },
  file:       { fill: '#636D83', border: '#7A8494' },
  folder:     { fill: '#4B5263', border: '#636D83' },
  community:  { fill: '#BE85C8', border: '#D0A1D8' },
  process:    { fill: '#5EA8A0', border: '#79BBB4' },
};

const DEFAULT_NODE_FILL = '#4a5a6a';
const DEFAULT_NODE_BORDER = '#5a6a7a';
const DEFAULT_EDGE_COLOR = '#2a3a4d';

/**
 * Build a Graphology MultiDirectedGraph from raw API node/edge arrays.
 *
 * Nodes receive random initial positions (ForceAtlas2 will reposition them),
 * colors based on their label, and sizes based on their degree.
 *
 * Edges that reference missing nodes or duplicate keys are silently skipped
 * to tolerate inconsistent backend data.
 *
 * @param nodes - Array of graph nodes from the API.
 * @param edges - Array of graph edges from the API.
 * @returns A fully-attributed Graphology graph ready for Sigma.
 */
export function buildGraphology(nodes: GraphNode[], edges: GraphEdge[]): MultiDirectedGraph {
  const graph = new MultiDirectedGraph();

  for (const node of nodes) {
    const palette = NODE_COLORS[node.label] ?? { fill: DEFAULT_NODE_FILL, border: DEFAULT_NODE_BORDER };
    graph.addNode(node.id, {
      label: node.name,
      x: (Math.random() - 0.5) * 1000,
      y: (Math.random() - 0.5) * 1000,
      size: 3,
      color: palette.fill,
      borderColor: palette.border,
      nodeType: node.label as NodeLabel,
      filePath: node.filePath,
      startLine: node.startLine,
      endLine: node.endLine,
      signature: node.signature,
      language: node.language,
      className: node.className,
      isDead: node.isDead,
      isEntryPoint: node.isEntryPoint,
      isExported: node.isExported,
      directory: node.filePath ? node.filePath.split('/').slice(0, -1).join('/') : '',
    });
  }

  for (const edge of edges) {
    if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) {
      continue;
    }
    try {
      graph.addEdgeWithKey(edge.id, edge.source, edge.target, {
        edgeType: edge.type,
        color: DEFAULT_EDGE_COLOR,
        size: 0.5,
        confidence: edge.confidence,
        strength: edge.strength,
        stepNumber: edge.stepNumber,
      });
    } catch {
      // Skip duplicate edge keys silently.
    }
  }

  // Assign node sizes: base 3, scaled by degree with gentle curve.
  // Classes/interfaces get a slight boost for visual hierarchy.
  graph.forEachNode((id, attrs) => {
    const degree = graph.degree(id);
    const nodeType = attrs.nodeType as string;
    const isClass = nodeType === 'class' || nodeType === 'interface';
    const base = isClass ? 5 : 3;
    graph.setNodeAttribute(id, 'size', base + Math.min(12, Math.sqrt(degree) * 1.5));
  });

  return graph;
}
