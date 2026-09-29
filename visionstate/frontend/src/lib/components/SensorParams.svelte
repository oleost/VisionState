<script lang="ts">
  // How the AI result becomes a Home Assistant state. Limits come from the backend config.
  import { app } from '../app.svelte';

  let { threshold = $bindable(), debounce = $bindable() }: { threshold: number; debounce: number } = $props();

  const limits = $derived(app.config?.sensor_limits);
  const unknown = $derived(app.config?.unknown_state ?? 'unknown');
</script>

{#if limits}
  <div class="col params">
    <label class="line">
      <span>Report <span class="mono">{unknown}</span> when the AI is less sure than</span>
      <span class="row"
        ><input
          class="input sm num"
          type="number"
          min={limits.threshold[0] * 100}
          max={limits.threshold[1] * 100}
          step="5"
          value={Math.round(threshold * 100)}
          oninput={(e) => (threshold = Number(e.currentTarget.value) / 100)}
        /> %</span
      >
    </label>
    <label class="line">
      <span>Change state after this many matching results in a row</span>
      <span class="row"
        ><input class="input sm num" type="number" min={limits.debounce[0]} max={limits.debounce[1]} step="1" bind:value={debounce} /></span
      >
    </label>
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
