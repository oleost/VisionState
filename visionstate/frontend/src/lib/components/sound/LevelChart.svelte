<script lang="ts">
  // The level of the last minute (one bar per half second, newest on the right) and the line a
  // window must reach to be analysed: what the sensor hears, and what it ignores as too quiet.
  import { db } from '../../sound';

  let { levels, line, floor = -90 }: { levels: number[]; line: number | null; floor?: number } = $props();

  const top = 0;
  const height = 80;
  const y = (value: number) => height - ((Math.max(floor, Math.min(top, value)) - floor) / (top - floor)) * height;
  const count = $derived(Math.max(levels.length, 1));
  const now = $derived(levels.length ? levels[levels.length - 1] : null);
</script>

<div class="col chart">
  <svg viewBox="0 0 {count} {height}" preserveAspectRatio="none" role="img" aria-label="Sound level of the last minute">
    {#each levels as level, i (i)}
      <rect x={i + 0.1} width="0.8" y={y(level)} height={height - y(level)} class:loud={line !== null && level >= line} />
    {/each}
    {#if line !== null}<line x1="0" x2={count} y1={y(line)} y2={y(line)} vector-effect="non-scaling-stroke" />{/if}
  </svg>
  <div class="row small legend">
    <span>Now <span class="mono">{db(now)}</span></span>
    <span class="spacer"></span>
    <span class="muted">{line === null ? 'Learning the background…' : `Analysed above ${db(line)}`}</span>
  </div>
</div>

<style>
  .chart {
    gap: var(--space-2);
  }
  svg {
    width: 100%;
    height: 96px;
    border-radius: var(--radius-sm);
    background: var(--c-sunken);
  }
  rect {
    fill: var(--c-border-strong);
  }
  rect.loud {
    fill: var(--c-accent);
  }
  line {
    stroke: var(--c-warn);
    stroke-width: 1.5;
    stroke-dasharray: 4 3;
  }
  .legend {
    gap: var(--space-3);
  }
</style>
