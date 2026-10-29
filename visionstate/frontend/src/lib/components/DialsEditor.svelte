<script lang="ts">
  // A mechanical counter's pointer dials, placed on its meter face (docs/POINTER_DIAL_PLAN.md). The
  // face is shown straightened the way the counter is read (the backend's imaging.meter_face), with
  // the counter's region outlined; each dial is a circle on it: drag it to move it, drag its handle to
  // size it. The first dial is ×0.1 of the last wheel, the next ×0.01 and so on. Mouse and touch
  // (pointer events), like the region editor.
  import { app } from '../app.svelte';
  import type { Dial, DialRead, ReadPreview } from '../types';
  import { DIAL_NEW } from '../ui';
  import Icon from './Icon.svelte';

  type Drag = { mode: 'move' | 'size'; index: number; start: [number, number]; orig: Dial };

  let {
    preview,
    dials = $bindable(),
    decimals,
  }: { preview: ReadPreview | null; dials: Dial[]; decimals: number } = $props();

  const limits = $derived(app.config!.reading_dials);
  const box = $derived(preview?.face_box ?? null);
  const regionWidth = $derived(preview?.region_width ?? 1);
  const reads = $derived<DialRead[]>(preview?.dials ?? []);
  // Read positions belong to the dials they were read with: only shown while the count matches.
  const readsMatch = $derived(reads.length === dials.length);

  let surface = $state<HTMLDivElement>();
  let drag: Drag | null = null;
  let selected = $state(-1);

  /** ×0.1, ×0.01 …: what one turn of the dial is worth, in the counter's unit. */
  const factor = (k: number) => `×${(10 ** -(decimals + k + 1)).toFixed(decimals + k + 1)}`;

  function planePoint(e: PointerEvent): [number, number] {
    const r = surface!.getBoundingClientRect();
    const [x0, y0, x1, y1] = box!;
    return [x0 + ((e.clientX - r.left) / r.width) * (x1 - x0), y0 + ((e.clientY - r.top) / r.height) * (y1 - y0)];
  }

  const round = (n: number) => Math.round(n * 1e4) / 1e4;
  const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));

  /** A dial kept on the face shown and within the limits the backend takes. */
  function kept(d: Dial): Dial {
    const [x0, y0, x1, y1] = box!;
    const reach = limits.dial_reach;
    const r = clamp(d.r, limits.dial_radius[0], limits.dial_radius[1]);
    return {
      x: round(clamp(d.x, Math.max(x0, -reach), Math.min(x1, regionWidth + reach))),
      y: round(clamp(d.y, Math.max(y0, -reach), Math.min(y1, 1 + reach))),
      r: round(r),
      ccw: d.ccw,
    };
  }

  function start(e: PointerEvent, index: number, mode: Drag['mode']) {
    if (!box) return;
    e.stopPropagation();
    e.preventDefault();
    try {
      surface!.setPointerCapture(e.pointerId);
    } catch {
      /* pointer no longer active: moves still bubble to the surface */
    }
    selected = index;
    drag = { mode, index, start: planePoint(e), orig: { ...dials[index] } };
  }

  function move(e: PointerEvent) {
    if (!drag) return;
    const p = planePoint(e);
    const { orig, index } = drag;
    const next =
      drag.mode === 'move'
        ? { ...orig, x: orig.x + p[0] - drag.start[0], y: orig.y + p[1] - drag.start[1] }
        : { ...orig, r: Math.hypot(p[0] - orig.x, p[1] - orig.y) };
    dials[index] = kept(next);
  }

  function end() {
    drag = null;
  }

  /** A new dial next to the last one (the next decimal is usually beside it), or below the counter. */
  function add() {
    if (!box || dials.length >= limits.max_dials) return;
    const last = dials.at(-1);
    const r = last?.r ?? DIAL_NEW.radius;
    const spot = last ? { x: last.x + r * DIAL_NEW.gap, y: last.y } : { x: regionWidth / 2, y: 1 + r * DIAL_NEW.gap * 0.7 };
    dials = [...dials, kept({ ...spot, r, ccw: last?.ccw ?? false })];
    selected = dials.length - 1;
  }

  function remove(index: number) {
    dials = dials.filter((_, i) => i !== index);
    selected = -1;
  }

  /** Where the needle points, drawn from the centre (0 at the top, in the dial's direction). */
  function needle(d: Dial, read: DialRead | undefined) {
    if (!read || read.needle === null) return null;
    const a = ((d.ccw ? -1 : 1) * read.needle * Math.PI) / 5;
    return { x: d.x + Math.sin(a) * d.r * 0.8, y: d.y - Math.cos(a) * d.r * 0.8 };
  }
</script>

{#if preview?.face && box}
  <div class="col" style="gap:var(--space-2)">
    <div
      class="surface"
      bind:this={surface}
      onpointermove={move}
      onpointerup={end}
      onpointercancel={end}
      role="presentation"
      data-testid="dials-editor"
    >
      <img src={preview.face} alt="The meter face, straightened" draggable="false" />
      <svg viewBox="{box[0]} {box[1]} {box[2] - box[0]} {box[3] - box[1]}" preserveAspectRatio="none" aria-hidden="true">
        <rect x="0" y="0" width={regionWidth} height="1" class="region" />
        {#each dials as d, i (i)}
          {@const n = readsMatch ? needle(d, reads[i]) : null}
          <circle cx={d.x} cy={d.y} r={d.r} class="dial" class:selected={selected === i} onpointerdown={(e) => start(e, i, 'move')} role="presentation" />
          {#if n}<line x1={d.x} y1={d.y} x2={n.x} y2={n.y} class="needle" />{/if}
        {/each}
      </svg>
      {#each dials as d, i (i)}
        <span
          class="label"
          style:left="{((d.x - box[0]) / (box[2] - box[0])) * 100}%"
          style:top="{((d.y - d.r - box[1]) / (box[3] - box[1])) * 100}%"
          data-testid="dial-label">{factor(i)}</span
        >
        <span
          class="handle"
          style:left="{((d.x + d.r - box[0]) / (box[2] - box[0])) * 100}%"
          style:top="{((d.y - box[1]) / (box[3] - box[1])) * 100}%"
          onpointerdown={(e) => start(e, i, 'size')}
          title="Drag to size the dial"
          role="presentation"
          data-testid="dial-size"
        ></span>
      {/each}
    </div>
    <p class="help">
      Put a circle on each pointer dial, the first on the ×0.1 dial: drag a circle to move it, its handle to size it.
      The dashed box is the counter.
    </p>
    <div class="row wrap" style="gap:8px">
      <button class="btn sm" onclick={add} disabled={dials.length >= limits.max_dials} data-testid="dial-add">
        <Icon name="plus" size={14} /> Add dial
      </button>
      {#if !dials.length}<span class="xsmall faint">No pointer dials: the counter is read from its wheels only.</span>{/if}
    </div>
    {#each dials as d, i (i)}
      {@const read = readsMatch ? reads[i] : undefined}
      <div class="dial-row" class:selected={selected === i} data-testid="dial-row">
        <strong class="mono small">{factor(i)}</strong>
        <span class="xsmall muted grow">
          {#if !read}
            Not read yet
          {:else if read.needle === null}
            <span class="warn-text">No needle found</span>
          {:else}
            Needle at <span class="mono">{read.needle.toFixed(1)}</span>
          {/if}
        </span>
        <button class="btn sm" onclick={() => (dials[i] = { ...d, ccw: !d.ccw })} title="Which way the dial's numbers go" aria-pressed={d.ccw}>
          {d.ccw ? 'Anticlockwise' : 'Clockwise'}
        </button>
        <button class="btn sm ghost" onclick={() => remove(i)} aria-label="Remove the {factor(i)} dial">
          <Icon name="trash" size={14} />
        </button>
      </div>
    {/each}
  </div>
{:else}
  <p class="small muted">The meter face shows here once the counter has been read.</p>
{/if}

<style>
  .surface {
    --handle: 16px;
    position: relative;
    overflow: hidden;
    background: var(--c-sunken);
    border-radius: var(--radius-sm);
    user-select: none;
    -webkit-user-select: none;
    -webkit-touch-callout: none;
    touch-action: none;
  }
  @media (pointer: coarse) {
    .surface {
      --handle: 26px;
    }
  }
  img {
    display: block;
    width: 100%;
    height: auto;
    pointer-events: none;
  }
  svg {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
  }
  .region {
    fill: none;
    stroke: var(--c-accent);
    stroke-width: 2px;
    stroke-dasharray: 6 5;
    vector-effect: non-scaling-stroke;
  }
  .dial {
    fill: var(--c-accent);
    fill-opacity: 0.12;
    stroke: var(--c-accent);
    stroke-width: 2px;
    vector-effect: non-scaling-stroke;
    pointer-events: all;
    cursor: move;
  }
  .dial.selected {
    stroke-width: 3px;
  }
  .needle {
    stroke: var(--c-badge);
    stroke-width: 3px;
    stroke-linecap: round;
    vector-effect: non-scaling-stroke;
  }
  .label {
    position: absolute;
    transform: translate(-50%, -115%);
    padding: 0 6px;
    border-radius: var(--radius-pill);
    background: var(--c-accent);
    color: var(--c-accent-ink);
    font: 700 var(--fs-xs) / 1.5 var(--font-mono);
    pointer-events: none;
    white-space: nowrap;
  }
  .handle {
    position: absolute;
    width: var(--handle);
    height: var(--handle);
    transform: translate(-50%, -50%);
    box-sizing: border-box;
    border-radius: 999px;
    background: var(--c-bg);
    border: 2px solid var(--c-accent);
    cursor: ew-resize;
    touch-action: none;
  }
  .help {
    margin: 0;
    font-size: var(--fs-sm);
    color: var(--c-faint);
  }
  .dial-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    padding: 6px 8px;
    border: 1px solid var(--c-border);
    border-radius: var(--radius-sm);
  }
  .dial-row.selected {
    border-color: var(--c-accent-line);
  }
  .grow {
    flex: 1 1 8em;
  }
  .warn-text {
    color: var(--c-warn);
  }
</style>
