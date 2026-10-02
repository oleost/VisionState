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
  // Four corners that sit on the bounding box are a plain rectangle again (e.g. after removing an
  // added corner), so it is stored and shown as one.
  const EPS = 1e-6;
  const onCorner = (p: Pt) =>
    (Math.abs(p[0] - x) < EPS || Math.abs(p[0] - x - w) < EPS) && (Math.abs(p[1] - y) < EPS || Math.abs(p[1] - y - h) < EPS);
  if (points.length === 4 && points.every(onCorner)) return { x, y, w, h };
  return { x, y, w, h, points };
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
