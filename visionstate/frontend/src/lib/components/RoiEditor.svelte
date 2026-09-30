<script lang="ts">
  // Shows an image with a region of interest (rectangle or polygon). When editable:
  // drag on empty space to draw a rectangle, drag the shape to move it, drag a corner to move
  // just that corner, drag a "+" on an edge to add a corner, and double-click or long-press a
  // corner to remove it. Works with mouse and touch (pointer events).
  import type { Snippet } from 'svelte';
  import { app } from '../app.svelte';
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
    children,
  }: { src: string | null; roi?: Roi | null; editable?: boolean; dim?: boolean; children?: Snippet } = $props();

  const MIN_SIZE = 0.02; // smallest region, as a share of the image
  const LONG_PRESS_MS = 600; // hold a corner this long (without moving) to remove it
  const MOVE_TOLERANCE = 0.01; // pointer movement that still counts as "not moved"
  const DOUBLE_TAP_MS = 400; // two presses on the same corner within this time remove it

  const maxPoints = $derived(app.config?.roi_max_points ?? 32);
  const points = $derived<Pt[]>(roi ? toPoints(roi) : []);
  const edges = $derived(points.length && points.length < maxPoints ? midpoints(points) : []);
  const polygonAttr = $derived(points.map((p) => `${p[0]},${p[1]}`).join(' '));
  const dimPath = $derived(
    points.length ? `M0 0H1V1H0Z M${points.map((p) => `${p[0]} ${p[1]}`).join(' L')}Z` : '',
  );

  let box: HTMLDivElement;
  let drag: Drag | null = null;
  let pressTimer: ReturnType<typeof setTimeout> | undefined;
  let lastPress = { index: -1, time: 0 };

  function point(e: PointerEvent): Pt {
    const r = box.getBoundingClientRect();
    return [clamp01((e.clientX - r.left) / r.width), clamp01((e.clientY - r.top) / r.height)];
  }

  function capture(e: PointerEvent) {
    e.stopPropagation();
    e.preventDefault();
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
    if (points.length <= 3) return;
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
    drag = null;
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
    {#if src}
      <img {src} alt="Camera frame" draggable="false" />
    {:else}
      <div class="placeholder"></div>
    {/if}

    {#if points.length}
      <svg class="shape" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true">
        {#if dim}<path d={dimPath} fill-rule="evenodd" class="dim" />{/if}
        <polygon points={polygonAttr} class="outline" class:solid={editable} onpointerdown={startMove} role="presentation" />
      </svg>
      {#if editable}
        {#each edges as m, i (i)}
          <span
            class="handle add"
            style:left="{m[0] * 100}%"
            style:top="{m[1] * 100}%"
            onpointerdown={(e) => startInsert(e, i)}
            title="Drag to add a corner"
            role="presentation">+</span
          >
        {/each}
        {#each points as p, i (i)}
          <span
            class="handle corner"
            style:left="{p[0] * 100}%"
            style:top="{p[1] * 100}%"
            onpointerdown={(e) => startVertex(e, i)}
            title="Drag to move · double-click, double-tap or hold to remove"
            role="presentation"
          ></span>
        {/each}
      {/if}
    {/if}
    {@render children?.()}
  </div>
  {#if editable}
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
    touch-action: none;
  }
  @media (pointer: coarse) {
    .roi {
      --handle: 26px; /* bigger targets for fingers */
    }
  }
  .roi.editable {
    cursor: crosshair;
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
