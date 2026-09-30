<script lang="ts">
  // When an object sensor reports an object. Limits come from the backend config.
  import { app } from '../app.svelte';

  let {
    threshold = $bindable(),
    debounce = $bindable(),
    clearAfter = $bindable(),
    minSize = $bindable(),
  }: { threshold: number; debounce: number; clearAfter: number; minSize: number } = $props();

  const limits = $derived(app.config?.sensor_limits);
  const objectLimits = $derived(app.config?.object_limits);
</script>

{#if limits && objectLimits}
  <div class="col params">
    <label class="line">
      <span>Count an object when the AI is at least</span>
      <span class="row"
        ><input
          class="input sm num"
          type="number"
          min={limits.threshold[0] * 100}
          max={limits.threshold[1] * 100}
          step="5"
          value={Math.round(threshold * 100)}
          oninput={(e) => (threshold = Number(e.currentTarget.value) / 100)}
        /> % sure</span
      >
    </label>
    <label class="line">
      <span>Report it after this many checks in a row</span>
      <span class="row"
        ><input class="input sm num" type="number" min={limits.debounce[0]} max={limits.debounce[1]} step="1" bind:value={debounce} /></span
      >
    </label>
    <label class="line">
      <span>Clear it when not seen for</span>
      <span class="row"
        ><input
          class="input sm num"
          type="number"
          min={objectLimits.clear_after_s[0]}
          max={objectLimits.clear_after_s[1]}
          step="5"
          bind:value={clearAfter}
        /> s</span
      >
    </label>
    <label class="line">
      <span>Ignore objects smaller than</span>
      <span class="row"
        ><input
          class="input sm num"
          type="number"
          min={objectLimits.min_size[0] * 100}
          max={objectLimits.min_size[1] * 100}
          step="0.5"
          value={Math.round(minSize * 1000) / 10}
          oninput={(e) => (minSize = Number(e.currentTarget.value) / 100)}
        /> % of the region</span
      >
    </label>
    <p class="xsmall muted">
      An object counts when the bottom of its box (where it stands) is inside the region. “Clear it” keeps a person
      detected while they turn around or are briefly hidden.
    </p>
  </div>
{/if}

<style>
  .params {
    gap: var(--space-3);
  }
  .line {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-4);
    font-size: var(--fs-base);
    color: var(--c-text-2);
  }
</style>
