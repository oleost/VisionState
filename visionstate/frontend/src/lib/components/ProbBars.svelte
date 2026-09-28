<script lang="ts">
  import type { StateDef } from '../types';

  let {
    states,
    probs,
    threshold,
  }: { states: StateDef[]; probs: Record<string, number>; threshold: number } = $props();
</script>

<div class="bars">
  {#each states as s (s.key)}
    {@const p = probs[s.key] ?? 0}
    <div class="bar">
      <div class="row small"><span>{s.name}</span><span class="spacer"></span><span class="mono muted">{Math.round(p * 100)}%</span></div>
      <div class="track">
        <div class="fill" style:width="{p * 100}%" style:background={s.color}></div>
        <div class="threshold" style:left="{threshold * 100}%"></div>
      </div>
    </div>
  {/each}
</div>

<style>
  .bars {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .bar {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .track {
    position: relative;
    height: 8px;
    border-radius: 4px;
    background: var(--c-surface-3);
  }
  .fill {
    height: 8px;
    border-radius: 4px;
    transition: width 0.3s;
  }
  .threshold {
    position: absolute;
    top: -3px;
    width: 2px;
    height: 14px;
    background: var(--c-text);
    opacity: 0.5;
  }
</style>
