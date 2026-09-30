<script lang="ts">
  // When a reading sensor accepts a value. Limits come from the backend config.
  import { app } from '../app.svelte';
  import type { ReadingMode } from '../types';

  let {
    threshold = $bindable(),
    debounce = $bindable(),
    maxStep = $bindable(),
    mode,
    unit,
  }: { threshold: number; debounce: number; maxStep: number; mode: ReadingMode; unit: string } = $props();

  const limits = $derived(app.config?.sensor_limits);
  const stepLimits = $derived(app.config?.reading_limits.max_step ?? [0, 1e9]);
</script>

{#if limits}
  <div class="col params">
    <label class="line">
      <span>Accept a reading when the reader is at least</span>
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
      <span>Publish a new value after this many equal readings in a row</span>
      <span class="row"
        ><input class="input sm num" type="number" min={limits.debounce[0]} max={limits.debounce[1]} step="1" bind:value={debounce} /></span
      >
    </label>
    <label class="line">
      <span>Reject a reading that changes more than (0 = no limit)</span>
      <span class="row"
        ><input class="input sm num" type="number" min={stepLimits[0]} max={stepLimits[1]} step="any" bind:value={maxStep} />
        {mode === 'time_left' ? 'min' : unit}</span
      >
    </label>
    <p class="xsmall muted">
      Rejected readings keep the last value and are listed in the history.
      {#if mode === 'counter'}A counter that reads lower than before is always rejected.{/if}
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
