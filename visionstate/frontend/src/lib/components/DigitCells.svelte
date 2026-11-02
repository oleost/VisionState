<script lang="ts">
  // The fields a mechanical counter's region is split into, one per digit wheel, drawn over an
  // image (place inside RoiEditor, which is the positioned frame). The text reader only looks at
  // the lighter middle of each field (the dividers between the wheels are left out); the wheel
  // reader (``whole``) looks at the whole field. A ``straightened`` region's fields follow its
  // corners in perspective, as they are read, and stay inside it.
  import { app } from '../app.svelte';
  import { isPolygon, quadMap, toPoints } from '../roi';
  import type { Roi } from '../types';

  let {
    roi,
    digits,
    whole = false,
    straightened = false,
  }: { roi: Roi | null; digits: number; whole?: boolean; straightened?: boolean } = $props();

  const limits = $derived(app.config?.reading_limits.digits ?? [1, 12]);
  const count = $derived(Math.min(limits[1], Math.max(limits[0], Math.round(digits) || limits[0])));
  // No region = the whole frame.
  const box = $derived(roi ?? { x: 0, y: 0, w: 1, h: 1 });
  const share = $derived(whole ? 1 : (app.config?.reading_counter_cell_share ?? 1));
  const map = $derived(straightened && roi && isPolygon(roi) && toPoints(roi).length === 4 ? quadMap(toPoints(roi)) : null);

  /** A piece of the upright region from u1 to u2 of its width, as an SVG polygon in the image. */
  function piece(to: (u: number, v: number) => number[], u1: number, u2: number): string {
    return [to(u1, 0), to(u2, 0), to(u2, 1), to(u1, 1)].map((p) => p.join(',')).join(' ');
  }
</script>

{#if map}
  <svg class="cells-quad" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true" data-testid="digit-cells">
    {#each { length: count } as _, i (i)}
      <g class="cell">
        <polygon class="read" points={piece(map, (i + 0.5 - share / 2) / count, (i + 0.5 + share / 2) / count)} />
        {#if i}
          {@const [a, b] = [map(i / count, 0), map(i / count, 1)]}
          <line class="divider" x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} />
        {/if}
      </g>
    {/each}
  </svg>
{:else if !(straightened && isPolygon(roi))}
  <div
    class="cells"
    data-testid="digit-cells"
    style:left="{box.x * 100}%"
    style:top="{box.y * 100}%"
    style:width="{box.w * 100}%"
    style:height="{box.h * 100}%"
  >
    {#each { length: count } as _, i (i)}
      <span class="cell"><span class="read" style:width="{share * 100}%"></span></span>
    {/each}
  </div>
{/if}

<style>
  .cells {
    position: absolute;
    display: flex;
    pointer-events: none;
  }
  .cells-quad {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
  }
  .divider {
    stroke: var(--c-accent);
    stroke-width: 1px;
    vector-effect: non-scaling-stroke;
  }
  polygon.read {
    fill: color-mix(in srgb, var(--c-accent) 14%, transparent);
  }
  .cell {
    flex: 1 1 0;
    min-width: 0;
    display: flex;
    justify-content: center;
    border-left: 1px solid var(--c-accent);
  }
  .cell:first-child {
    border-left: none;
  }
  span.read {
    background: color-mix(in srgb, var(--c-accent) 14%, transparent);
  }
</style>
