/**
 * Computes convex hull outlines for community clusters.
 *
 * Given a Graphology graph and a list of communities, this module calculates
 * the convex hull polygon for each community's member nodes (based on their
 * current graph positions) and returns the hull data for rendering.
 *
 * The convex hull is computed using the Graham scan algorithm.
 */

import type { AbstractGraph, Attributes } from 'graphology-types';
import type { Community } from '@/types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Point {
  x: number;
  y: number;
}

export interface HullData {
  communityId: string;
  name: string;
  color: string;
  /** Ordered vertices of the convex hull polygon. */
  points: Point[];
  /** Geometric centroid of the hull. */
  centroid: Point;
}

// ---------------------------------------------------------------------------
// Color palette for community hulls (12 distinct colors, will cycle)
// ---------------------------------------------------------------------------

const HULL_COLORS = [
  '#39d353', '#58a6ff', '#a371f7', '#3fb8af', '#f0883e', '#d4a72c',
  '#f85149', '#56d4dd', '#6b7d8e', '#e5a839', '#a5d6ff', '#c9d1d9',
];

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Compute convex hull data for each community.
 *
 * For communities with fewer than 3 positioned nodes, no hull is generated
 * (they are skipped).  Colors are assigned cyclically from `HULL_COLORS`.
 *
 * @param graph - A Graphology graph instance with positioned nodes (x, y).
 * @param communities - Array of community definitions with member node IDs.
 * @returns Array of hull data objects, one per community with enough nodes.
 */
export function computeHulls(
  graph: AbstractGraph<Attributes, Attributes, Attributes>,
  communities: Community[],
): HullData[] {
  const hulls: HullData[] = [];

  for (let i = 0; i < communities.length; i++) {
    const community = communities[i];
    const points: Point[] = [];

    for (const memberId of community.members) {
      if (!graph.hasNode(memberId)) continue;

      const attrs = graph.getNodeAttributes(memberId);
      const x = attrs.x as number | undefined;
      const y = attrs.y as number | undefined;

      if (x !== undefined && y !== undefined && isFinite(x) && isFinite(y)) {
        points.push({ x, y });
      }
    }

    // Need at least 3 points to form a meaningful hull.
    if (points.length < 3) continue;

    const hull = convexHull(points);
    if (hull.length < 3) continue;

    const centroid = computeCentroid(hull);

    hulls.push({
      communityId: community.id,
      name: community.name,
      color: HULL_COLORS[i % HULL_COLORS.length],
      points: hull,
      centroid,
    });
  }

  return hulls;
}

// ---------------------------------------------------------------------------
// Graham scan convex hull
// ---------------------------------------------------------------------------

/**
 * Compute the convex hull of a set of 2D points using the Graham scan.
 *
 * @returns The vertices of the convex hull in counter-clockwise order.
 */
function convexHull(points: Point[]): Point[] {
  if (points.length < 3) return [...points];

  // Find the lowest point (and leftmost if tied).
  let pivot = points[0];
  for (let i = 1; i < points.length; i++) {
    const p = points[i];
    if (p.y < pivot.y || (p.y === pivot.y && p.x < pivot.x)) {
      pivot = p;
    }
  }

  // Sort points by polar angle with respect to the pivot.
  const sorted = points
    .filter((p) => p !== pivot)
    .sort((a, b) => {
      const angleA = Math.atan2(a.y - pivot.y, a.x - pivot.x);
      const angleB = Math.atan2(b.y - pivot.y, b.x - pivot.x);
      if (angleA !== angleB) return angleA - angleB;
      // If same angle, closer point first.
      const distA = (a.x - pivot.x) ** 2 + (a.y - pivot.y) ** 2;
      const distB = (b.x - pivot.x) ** 2 + (b.y - pivot.y) ** 2;
      return distA - distB;
    });

  const stack: Point[] = [pivot];

  for (const point of sorted) {
    // Remove points that make a clockwise turn.
    while (stack.length >= 2) {
      const top = stack[stack.length - 1];
      const belowTop = stack[stack.length - 2];
      if (cross(belowTop, top, point) <= 0) {
        stack.pop();
      } else {
        break;
      }
    }
    stack.push(point);
  }

  return stack;
}

/** Cross product of vectors (O->A) and (O->B). Positive = counter-clockwise. */
function cross(o: Point, a: Point, b: Point): number {
  return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
}

// ---------------------------------------------------------------------------
// Centroid
// ---------------------------------------------------------------------------

/** Compute the geometric centroid (average of all points) of a polygon. */
function computeCentroid(points: Point[]): Point {
  let cx = 0;
  let cy = 0;
  for (const p of points) {
    cx += p.x;
    cy += p.y;
  }
  return {
    x: cx / points.length,
    y: cy / points.length,
  };
}
