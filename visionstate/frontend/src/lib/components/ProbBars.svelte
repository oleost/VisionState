<script lang="ts">
  // How sure the AI is of each state or sound, with the threshold it must pass marked on each bar.
  // A state sensor has one threshold; a sound sensor one per sound. `tags` adds badges to a bar.
  import type { Snippet } from 'svelte';

  type Bar = { key: string; name: string; color: string; value: number; threshold?: number };
  let { bars, threshold = 0, tags }: { bars: Bar[]; threshold?: number; tags?: Snippet<[string]> } = $props();
</script>

<div class="bars">
  {#each bars as b (b.key)}
    {@const limit = b.threshold ?? threshold}
    <div class="bar" data-testid="bar-{b.key}">
      <div class="row small head">
        <span>{b.name}</span>
        {@render tags?.(b.key)}
        <span class="spacer"></span>
        <span class="mono muted">{Math.round(b.value * 100)}%</span>
      </div>
      <div class="track">
        <div class="fill" style:width="{b.value * 100}%" style:background={b.color}></div>
        <div class="threshold" style:left="{limit * 100}%" title="Threshold {Math.round(limit * 100)}%"></div>
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
  .head {
    gap: var(--space-2);
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
