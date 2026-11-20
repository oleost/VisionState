<script lang="ts">
  // Shows an image with a region of interest (rectangle or polygon). When editable:
  // drag on empty space to draw a rectangle, drag the shape to move it, drag a corner to move
  // just that corner, drag a "+" on an edge to add a corner, and double-click or long-press a
  // corner to remove it. Works with mouse and touch (pointer events).
  // `quad`: a region that is straightened (a reading sensor's): always four corners, its top edge
  // (the first) drawn thick and marked "Top" — dragging the corners turns it and undoes perspective.
  // While editing, the view zooms in on the region (with some of the picture round it), so its corners
  // are easy to place on a phone: when it opens with a region and when a new one has been drawn. The
  // button in its corner switches between that and the whole picture. ``overlay``: drawn over the
  // picture where it is (zoomed with it: digit fields, boxes); ``children``: over the frame (badges).
  import { untrack, type Snippet } from 'svelte';
  import { app } from '../app.svelte';
  import Icon from './Icon.svelte';
  import { clamp01, fromPoints, midpoints, toPoints, type Pt } from '../roi';
  import type { Roi } from '../types';

  type Drag =
    | { mode: 'new'; start: Pt }
    | { mode: 'move'; start: Pt; orig: Pt[] }
    | { mode: 'vertex'; index: number; start: Pt; orig: Pt[]; moved: boolean };

  let {
    src,
    roi = $bindable(null),
    editable = false,
    dim = true,
    quad = false,
    overlay,
    children,
  }: {
    src: string | null;
    roi?: Roi | null;
    editable?: boolean;
    dim?: boolean;
    quad?: boolean;
    overlay?: Snippet;
    children?: Snippet;
  } = $props();

  const MIN_SIZE = 0.02; // smallest region, as a share of the image
  const LONG_PRESS_MS = 600; // hold a corner this long (without moving) to remove it
  const MOVE_TOLERANCE = 0.01; // pointer movement that still counts as "not moved"
  const DOUBLE_TAP_MS = 400; // two presses on the same corner within this time remove it
  // Zoomed in on a region: as much picture round it on each side as ZOOM_MARGIN of its larger side,
  // plus ZOOM_PAD; not closer than ZOOM_MIN of the picture, and not at all when that would show
  // ZOOM_USELESS of it or more.
  const ZOOM_MARGIN = 0.15;
  const ZOOM_PAD = 0.03;
  const ZOOM_MIN = 0.2;
  const ZOOM_USELESS = 0.85;
  // The view moves only when the region let go of comes this close to its edge (a share of the view),
  // or when it has become so much smaller that the view could be closer by ZOOM_TIGHTER: small
  // adjustments leave it where it is.
  const ZOOM_EDGE = 0.06;
  const ZOOM_TIGHTER = 0.7;

  const maxPoints = $derived(app.config?.roi_max_points ?? 32);
  const points = $derived<Pt[]>(roi ? toPoints(roi) : []);
  const edges = $derived(points.length && points.length < maxPoints && !quad ? midpoints(points) : []);
  // The top edge of a straightened region, and where its label sits: just outside the edge's middle.
  const top = $derived.by(() => {
    if (!quad || points.length !== 4) return null;
    const [a, b] = points;
    const mid: Pt = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    const centre: Pt = [points.reduce((s, p) => s + p[0], 0) / 4, points.reduce((s, p) => s + p[1], 0) / 4];
    const out = Math.hypot(mid[0] - centre[0], mid[1] - centre[1]) || 1;
    const shift = 0.045;
    return { a, b, label: [mid[0] + ((mid[0] - centre[0]) / out) * shift, mid[1] + ((mid[1] - centre[1]) / out) * shift] as Pt };
  });
  const polygonAttr = $derived(points.map((p) => `${p[0]},${p[1]}`).join(' '));
  const dimPath = $derived(
    points.length ? `M0 0H1V1H0Z M${points.map((p) => `${p[0]} ${p[1]}`).join(' L')}Z` : '',
  );

  // Zoomed in (the button switches it). The view follows the region when it is let go of (and when
  // it changes otherwise: Turn, Reset) if it needs to, and stays put while it is dragged, so nothing
  // moves under the finger.
  type View = { x: number; y: number; s: number };
  let zoom = $state(true);
  let dragging = $state(false);
  // The part of the picture shown (a square in the picture's own 0-1 units, so it keeps its shape).
  let view = $state<View | null>(null);
  $effect(() => {
    if (dragging) return;
    const pts = points;
    const next = editable && zoom && src ? viewFor(pts) : null;
    untrack(() => {
      if (!next) view = null;
      else if (!view || outgrown(view, pts, next)) view = next;
    });
  });
  /** Whether the region has come close to the view's edge, or the view could be much closer. */
  function outgrown(v: View, pts: Pt[], next: View): boolean {
    const m = v.s * ZOOM_EDGE;
    // An edge of the view on the picture's own edge cannot move further: never near there.
    const [left, right, up, down] = [v.x > 0, v.x + v.s < 1, v.y > 0, v.y + v.s < 1];
    const near = pts.some(
      ([x, y]) => (left && x < v.x + m) || (right && x > v.x + v.s - m) || (up && y < v.y + m) || (down && y > v.y + v.s - m),
    );
    return near || next.s < v.s * ZOOM_TIGHTER;
  }

  function viewFor(pts: Pt[]): View | null {
    if (!pts.length) return null;
    const xs = pts.map((p) => p[0]);
    const ys = pts.map((p) => p[1]);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const side = Math.max(x1 - x0, y1 - y0);
    const s = Math.max(ZOOM_MIN, side * (1 + 2 * ZOOM_MARGIN) + 2 * ZOOM_PAD);
    if (s >= ZOOM_USELESS) return null;
    const fit = (centre: number) => Math.min(Math.max(centre - s / 2, 0), 1 - s);
    return { x: fit((x0 + x1) / 2), y: fit((y0 + y1) / 2), s };
  }
  const canZoom = $derived(editable && !!viewFor(points));
  /** Where a point of the picture is in the frame, in percent. */
  const at = (p: Pt): [number, number] =>
    view ? [((p[0] - view.x) / view.s) * 100, ((p[1] - view.y) / view.s) * 100] : [p[0] * 100, p[1] * 100];

  let box: HTMLDivElement;
  let drag: Drag | null = null;
  let pressTimer: ReturnType<typeof setTimeout> | undefined;
  let lastPress = { index: -1, time: 0 };

  function point(e: PointerEvent): Pt {
    const r = box.getBoundingClientRect();
    const [x, y] = [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height];
    return view ? [clamp01(view.x + x * view.s), clamp01(view.y + y * view.s)] : [clamp01(x), clamp01(y)];
  }

  function capture(e: PointerEvent) {
    e.stopPropagation();
    e.preventDefault();
    dragging = true;
    try {
      box.setPointerCapture(e.pointerId); // keep receiving moves when the pointer leaves the image
    } catch {
      /* pointer no longer active (e.g. synthetic events): moves still bubble to the box */
    }
  }

  function startNew(e: PointerEvent) {
    if (!editable) return;
    capture(e);
    drag = { mode: 'new', start: point(e) };
  }

  function startMove(e: PointerEvent) {
    if (!editable) return;
    capture(e);
    drag = { mode: 'move', start: point(e), orig: points };
  }

  function startVertex(e: PointerEvent, index: number) {
    if (!editable) return;
    capture(e);
    // Double-click / double-tap. (Pointer capture sends the native dblclick elsewhere, so detect it here.)
    const now = Date.now();
    if (lastPress.index === index && now - lastPress.time < DOUBLE_TAP_MS) {
      lastPress = { index: -1, time: 0 };
      removeVertex(index);
      return;
    }
    lastPress = { index, time: now };
    drag = { mode: 'vertex', index, start: point(e), orig: points, moved: false };
    clearTimeout(pressTimer);
    pressTimer = setTimeout(() => {
      if (drag?.mode === 'vertex' && !drag.moved) {
        removeVertex(index);
        drag = null;
        dragging = false;
      }
    }, LONG_PRESS_MS);
  }

  function startInsert(e: PointerEvent, edge: number) {
    if (!editable) return;
    const next = [...points];
    next.splice(edge + 1, 0, edges[edge]);
    roi = fromPoints(next);
    startVertex(e, edge + 1);
  }

  function removeVertex(index: number) {
    if (points.length <= 3 || quad) return;
    roi = fromPoints(points.filter((_, i) => i !== index));
  }

  function move(e: PointerEvent) {
    if (!drag) return;
    const p = point(e);
    if (drag.mode === 'new') {
      const [x1, y1] = drag.start;
      roi = { x: Math.min(x1, p[0]), y: Math.min(y1, p[1]), w: Math.abs(p[0] - x1), h: Math.abs(p[1] - y1) };
    } else if (drag.mode === 'move') {
      const xs = drag.orig.map((q) => q[0]);
      const ys = drag.orig.map((q) => q[1]);
      // Keep the whole shape inside the image.
      const dx = Math.min(Math.max(p[0] - drag.start[0], -Math.min(...xs)), 1 - Math.max(...xs));
      const dy = Math.min(Math.max(p[1] - drag.start[1], -Math.min(...ys)), 1 - Math.max(...ys));
      roi = fromPoints(drag.orig.map((q) => [q[0] + dx, q[1] + dy]));
    } else {
      if (Math.hypot(p[0] - drag.start[0], p[1] - drag.start[1]) > MOVE_TOLERANCE) drag.moved = true;
      const index = drag.index;
      roi = fromPoints(drag.orig.map((q, i) => (i === index ? p : q)));
    }
  }

  function end() {
    clearTimeout(pressTimer);
    if (drag?.mode === 'new' && roi && (roi.w < MIN_SIZE || roi.h < MIN_SIZE)) roi = null;
    if (roi && (roi.w < MIN_SIZE || roi.h < MIN_SIZE) && drag && drag.mode !== 'new') roi = fromPoints(drag.orig);
    if (drag?.mode === 'new' && roi) zoom = true; // placed: closer, to set its corners
    drag = null;
    dragging = false;
  }
</script>

<div class="wrap">
  <div
    class="roi"
    class:editable
    bind:this={box}
    onpointerdown={startNew}
    onpointermove={move}
    onpointerup={end}
    onpointercancel={end}
    role="presentation"
  >
    <div
      class="stage"
      style:transform={view ? `scale(${1 / view.s}) translate(${-view.x * 100}%, ${-view.y * 100}%)` : null}
    >
      {#if src}
        <img {src} alt="Camera frame" draggable="false" />
      {:else}
        <div class="placeholder"></div>
      {/if}
      {@render overlay?.()}
    </div>

    {#if points.length}
      <svg
        class="shape"
        viewBox={view ? `${view.x} ${view.y} ${view.s} ${view.s}` : '0 0 1 1'}
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        {#if dim}<path d={dimPath} fill-rule="evenodd" class="dim" />{/if}
        <polygon points={polygonAttr} class="outline" class:solid={editable} onpointerdown={startMove} role="presentation" />
        {#if top}<line x1={top.a[0]} y1={top.a[1]} x2={top.b[0]} y2={top.b[1]} class="top" />{/if}
      </svg>
      {#if top}
        <span class="top-label" style:left="{at(top.label)[0]}%" style:top="{at(top.label)[1]}%" data-testid="roi-top">Top</span>
      {/if}
      {#if editable}
        {#each edges as m, i (i)}
          <span
            class="handle add"
            style:left="{at(m)[0]}%"
            style:top="{at(m)[1]}%"
            onpointerdown={(e) => startInsert(e, i)}
            title="Drag to add a corner"
            role="presentation">+</span
          >
        {/each}
        {#each points as p, i (i)}
          <span
            class="handle corner"
            style:left="{at(p)[0]}%"
            style:top="{at(p)[1]}%"
            onpointerdown={(e) => startVertex(e, i)}
            title={quad ? 'Drag to move' : 'Drag to move · double-click, double-tap or hold to remove'}
            role="presentation"
          ></span>
        {/each}
      {/if}
    {/if}
    {#if editable && (view || canZoom)}
      <button
        type="button"
        class="btn sm zoom"
        onpointerdown={(e) => e.stopPropagation()}
        onclick={() => (zoom = !view)}
        data-testid="roi-zoom"
      >
        <Icon name={view ? 'zoom-out' : 'zoom-in'} size={14} />
        {view ? 'Whole picture' : 'Zoom in'}
      </button>
    {/if}
    {@render children?.()}
  </div>
  {#if editable && quad}
    <p class="help">
      Drag the corners onto the corners of what is read · the thick edge marked <strong>Top</strong> is its top.
    </p>
  {:else if editable}
    <p class="help">
      Drag a corner to move it · drag a <strong>+</strong> to add a corner · double-click / double-tap or hold a corner to remove it.
    </p>
  {/if}
</div>

<style>
  .roi {
    --handle: 16px;
    position: relative;
    overflow: hidden;
    background: var(--c-sunken);
    user-select: none;
    -webkit-user-select: none;
    -webkit-touch-callout: none; /* no "save image" menu on long-press */
  }
  @media (pointer: coarse) {
    .roi {
      --handle: 26px; /* bigger targets for fingers */
    }
  }
  .roi.editable {
    cursor: crosshair;
    touch-action: none; /* drawing, not scrolling — only while editing; a shown frame must scroll */
  }
  .stage {
    position: relative;
    transform-origin: 0 0;
  }
  .zoom {
    position: absolute;
    right: var(--space-2);
    bottom: var(--space-2);
    opacity: 0.92;
  }
  img {
    display: block;
    width: 100%;
    height: auto;
    pointer-events: none; /* touches go to the editor, never to the image itself */
  }
  .placeholder {
    aspect-ratio: 16 / 9;
  }
  .shape {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
  }
  .dim {
    fill: rgba(8, 10, 13, 0.5);
  }
  .outline {
    fill: transparent;
    stroke: var(--c-accent);
    stroke-width: 2px;
    stroke-dasharray: 6 5;
    vector-effect: non-scaling-stroke;
  }
  .editable .outline {
    pointer-events: all;
    cursor: move;
  }
  .outline.solid {
    stroke-dasharray: none;
  }
  .top {
    stroke: var(--c-accent);
    stroke-width: 6px;
    stroke-linecap: round;
    vector-effect: non-scaling-stroke;
  }
  .top-label {
    position: absolute;
    transform: translate(-50%, -50%);
    padding: 1px 7px;
    border-radius: var(--radius-pill);
    background: var(--c-accent);
    color: var(--c-accent-ink);
    font: 700 var(--fs-xs) / 1.4 var(--font-body);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    pointer-events: none;
  }
  .handle {
    position: absolute;
    width: var(--handle);
    height: var(--handle);
    transform: translate(-50%, -50%);
    box-sizing: border-box;
    touch-action: none;
  }
  .corner {
    background: var(--c-bg);
    border: 2px solid var(--c-accent);
    border-radius: 4px;
    cursor: grab;
  }
  .add {
    width: calc(var(--handle) * 0.9);
    height: calc(var(--handle) * 0.9);
    border-radius: 999px;
    background: var(--c-accent);
    color: var(--c-accent-ink);
    font: 700 calc(var(--handle) * 0.75) / 1 var(--font-body);
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0.75;
    cursor: copy;
  }
  .add:hover {
    opacity: 1;
  }
  .help {
    margin: 8px 0 0;
    font-size: var(--fs-sm);
    color: var(--c-faint);
  }
</style>
