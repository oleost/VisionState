<script lang="ts">
  // When a reading sensor accepts a value. Limits come from the backend config.
  import { app } from '../app.svelte';
  import type { ReadingMode } from '../types';

  let {
    threshold = $bindable(),
    debounce = $bindable(),
    maxStep = $bindable(),
    spotRate = $bindable(),
    rateWindow = $bindable(),
    mode,
    unit,
  }: {
    threshold: number;
    debounce: number;
    maxStep: number;
    spotRate: number;
    rateWindow: number;
    mode: ReadingMode;
    unit: string;
  } = $props();

  const limits = $derived(app.config?.sensor_limits);
  const stepLimits = $derived(app.config?.reading_limits.max_step ?? [0, 1e9]);
  const spotLimits = $derived(app.config?.reading_limits.spot_rate ?? [0, 1]);
  const windowLimits = $derived(app.config?.reading_limits.rate_window_min ?? [1, 1440]);
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
    <label class="line">
      <span class="col" style="gap:2px">
        <span>Spot-check accepted readings</span>
        <span class="xsmall faint">This share goes to the review queue too, to find misreads that passed every check.</span>
      </span>
      <span class="row"
        ><input
          class="input sm num"
          type="number"
          min={spotLimits[0] * 100}
          max={spotLimits[1] * 100}
          step="1"
          value={Math.round(spotRate * 100)}
          oninput={(e) => (spotRate = Number(e.currentTarget.value) / 100)}
          aria-label="Share of accepted readings to spot-check"
        /> %</span
      >
    </label>
    {#if mode === 'counter'}
      <label class="line">
        <span class="col" style="gap:2px">
          <span>Rate over the last</span>
          <span class="xsmall faint">For the <em>Rate</em> entity in Home Assistant (off there until you turn it on), e.g. to spot a leak.</span>
        </span>
        <span class="row"
          ><input
            class="input sm num"
            type="number"
            min={windowLimits[0]}
            max={windowLimits[1]}
            step="1"
            bind:value={rateWindow}
            aria-label="Rate over the last minutes"
          /> min</span
        >
      </label>
    {/if}
    <p class="xsmall muted">
      Rejected readings keep the last value; every one of them is listed in the history and waits in the review queue.
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
