// Geometry for regions of interest. Coordinates are normalised (0–1) relative to the image.
import type { Roi } from './types';

export type Pt = [number, number];

export const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

/** The four corners of a rectangle, clockwise from top-left. */
export function rectPoints(r: { x: number; y: number; w: number; h: number }): Pt[] {
  return [
    [r.x, r.y],
    [r.x + r.w, r.y],
    [r.x + r.w, r.y + r.h],
    [r.x, r.y + r.h],
  ];
}

/** Corners of a region: its polygon, or the rectangle's corners. */
export const toPoints = (roi: Roi): Pt[] => (roi.points?.length ? roi.points.map((p) => [p[0], p[1]]) : rectPoints(roi));

/** A region from polygon corners; x/y/w/h is the bounding box (the backend recomputes it too). */
export function fromPoints(points: Pt[]): Roi {
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const x = Math.min(...xs);
  const y = Math.min(...ys);
  const w = Math.max(...xs) - x;
  const h = Math.max(...ys) - y;
  // The bounding box's four corners, clockwise from its top-left, are a plain rectangle again (e.g.
  // after removing an added corner), so it is stored and shown as one. Starting elsewhere they stay
  // corners: a straightened region's top is its first edge (turnTop).
  const EPS = 1e-6;
  const same = (p: Pt, q: Pt) => Math.abs(p[0] - q[0]) < EPS && Math.abs(p[1] - q[1]) < EPS;
  const box = rectPoints({ x, y, w, h });
  if (points.length === 4 && points.every((p, i) => same(p, box[i]))) return { x, y, w, h };
  return { x, y, w, h, points };
}

/** A straightened region turned a quarter: the next edge clockwise becomes its top (the first edge,
 * from the first corner to the second). */
export function turnTop(roi: Roi): Roi {
  const points = toPoints(roi);
  return fromPoints([...points.slice(1), points[0]]);
}

/** The bounding rectangle of a region, without polygon corners. */
export const toRectangle = (roi: Roi): Roi => ({ x: roi.x, y: roi.y, w: roi.w, h: roi.h });

/** Midpoint of each edge (edge i runs from corner i to corner i+1). */
export const midpoints = (points: Pt[]): Pt[] =>
  points.map((p, i) => {
    const q = points[(i + 1) % points.length];
    return [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2];
  });

export const isPolygon = (roi: Roi | null | undefined) => !!roi?.points?.length;
