<script lang="ts">
  // Shows an image with a region-of-interest box. When editable, drag to draw, move or resize it.
  import type { Snippet } from 'svelte';
  import type { Roi } from '../types';

  type Mode = 'new' | 'move' | 'nw' | 'ne' | 'sw' | 'se';

  let {
    src,
    roi = $bindable(null),
    editable = false,
    dim = true,
    children,
  }: { src: string | null; roi?: Roi | null; editable?: boolean; dim?: boolean; children?: Snippet } = $props();

  const MIN_SIZE = 0.02;
  let box: HTMLDivElement;
  let drag: { mode: Mode; start: { x: number; y: number }; orig: Roi } | null = null;

  const clamp = (v: number, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, v));

  function point(e: PointerEvent) {
    const r = box.getBoundingClientRect();
    return { x: clamp((e.clientX - r.left) / r.width), y: clamp((e.clientY - r.top) / r.height) };
  }

  function start(e: PointerEvent, mode: Mode) {
    if (!editable) return;
    e.stopPropagation();
    e.preventDefault();
    const p = point(e);
    drag = { mode, start: p, orig: roi ?? { x: p.x, y: p.y, w: 0, h: 0 } };
    box.setPointerCapture(e.pointerId);
  }

  function move(e: PointerEvent) {
    if (!drag) return;
    const p = point(e);
    const o = drag.orig;
    if (drag.mode === 'move') {
      roi = { ...o, x: clamp(o.x + p.x - drag.start.x, 0, 1 - o.w), y: clamp(o.y + p.y - drag.start.y, 0, 1 - o.h) };
      return;
    }
    let [x1, y1, x2, y2] = [o.x, o.y, o.x + o.w, o.y + o.h];
    if (drag.mode === 'new') [x1, y1, x2, y2] = [drag.start.x, drag.start.y, p.x, p.y];
    if (drag.mode === 'nw') [x1, y1] = [p.x, p.y];
    if (drag.mode === 'ne') [x2, y1] = [p.x, p.y];
    if (drag.mode === 'sw') [x1, y2] = [p.x, p.y];
    if (drag.mode === 'se') [x2, y2] = [p.x, p.y];
    roi = { x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1), h: Math.abs(y2 - y1) };
  }

  function end() {
    if (drag && roi && (roi.w < MIN_SIZE || roi.h < MIN_SIZE)) roi = drag.mode === 'new' ? null : drag.orig;
    drag = null;
  }

  const handles: Mode[] = ['nw', 'ne', 'sw', 'se'];
</script>

<div
  class="roi"
  class:editable
  bind:this={box}
  onpointerdown={(e) => start(e, 'new')}
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
  {#if roi}
    <div
      class="rect"
      class:dim
      style:left="{roi.x * 100}%"
      style:top="{roi.y * 100}%"
      style:width="{roi.w * 100}%"
      style:height="{roi.h * 100}%"
      onpointerdown={(e) => start(e, 'move')}
      role="presentation"
    >
      {#if editable}
        {#each handles as h (h)}
          <span class="handle {h}" onpointerdown={(e) => start(e, h)} role="presentation"></span>
        {/each}
      {/if}
    </div>
  {/if}
  {@render children?.()}
</div>

<style>
  .roi {
    position: relative;
    overflow: hidden;
    background: var(--c-sunken);
    user-select: none;
    touch-action: none;
  }
  .roi.editable {
    cursor: crosshair;
  }
  img {
    width: 100%;
    height: auto;
  }
  .placeholder {
    aspect-ratio: 16 / 9;
  }
  .rect {
    position: absolute;
    border: 2px dashed var(--c-accent);
  }
  .rect.dim {
    box-shadow: 0 0 0 4000px rgba(8, 10, 13, 0.5);
  }
  .editable .rect {
    border-style: solid;
    cursor: move;
  }
  .handle {
    position: absolute;
    width: 16px;
    height: 16px;
    background: var(--c-bg);
    border: 2px solid var(--c-accent);
    border-radius: 4px;
  }
  .nw {
    left: -9px;
    top: -9px;
    cursor: nwse-resize;
  }
  .ne {
    right: -9px;
    top: -9px;
    cursor: nesw-resize;
  }
  .sw {
    left: -9px;
    bottom: -9px;
    cursor: nesw-resize;
  }
  .se {
    right: -9px;
    bottom: -9px;
    cursor: nwse-resize;
  }
</style>
