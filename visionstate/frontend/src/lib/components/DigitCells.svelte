<script lang="ts">
  // The fields a mechanical counter's region is split into, one per digit wheel, drawn over an
  // image (place inside RoiEditor, which is the positioned frame). The text reader only looks at
  // the lighter middle of each field (the dividers between the wheels are left out); the wheel
  // reader (``whole``) looks at the whole field.
  import { app } from '../app.svelte';
  import type { Roi } from '../types';

  let { roi, digits, whole = false }: { roi: Roi | null; digits: number; whole?: boolean } = $props();

  const limits = $derived(app.config?.reading_limits.digits ?? [1, 12]);
  const count = $derived(Math.min(limits[1], Math.max(limits[0], Math.round(digits) || limits[0])));
  // No region = the whole frame.
  const box = $derived(roi ?? { x: 0, y: 0, w: 1, h: 1 });
  const share = $derived(whole ? 1 : (app.config?.reading_counter_cell_share ?? 1));
</script>

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

<style>
  .cells {
    position: absolute;
    display: flex;
    pointer-events: none;
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
  .read {
    background: color-mix(in srgb, var(--c-accent) 14%, transparent);
  }
</style>
