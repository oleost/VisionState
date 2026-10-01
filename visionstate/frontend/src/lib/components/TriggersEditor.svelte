<script lang="ts">
  // "When to check": interval, trigger entities with follow-up burst, and optional change detection.
  // Defaults and limits come from the backend (GET /config → trigger_defaults / trigger_limits).
  import { app } from '../app.svelte';
  import { pct } from '../format';
  import type { Triggers } from '../types';
  import EntityPicker from './EntityPicker.svelte';

  let {
    triggers = $bindable(),
    interval_s = $bindable(),
    liveScore = null,
  }: { triggers: Triggers; interval_s: number; liveScore?: number | null } = $props();

  const limits = $derived(app.config?.trigger_limits);
  const intervalLimits = $derived(app.config?.sensor_limits.interval_s);
  const maxEntities = $derived(app.config?.trigger_max_entities ?? 20);
  const burstUsed = $derived(triggers.entities.length > 0 || triggers.change_detection);
</script>

{#if limits && intervalLimits}
  <div class="col triggers">
    <div class="block">
      <label class="line">
        <span class="col" style="gap:2px">
          <strong>Regular check</strong>
          <span class="xsmall faint">The safety net. Can be long when triggers are set up.</span>
        </span>
        <span class="row"
          ><span class="small muted">every</span>
          <input class="input sm num" type="number" min={intervalLimits[0]} max={intervalLimits[1]} step="1" bind:value={interval_s} /> s</span
        >
      </label>
    </div>

    <div class="block col">
      <span class="col" style="gap:2px">
        <strong>Check when these change</strong>
        <span class="xsmall faint">Motion sensors, door contacts, the garage opener… Any state change starts a check.</span>
      </span>
      <EntityPicker bind:value={triggers.entities} max={maxEntities} />
    </div>

    <div class="block col">
      <label class="check">
        <input type="checkbox" bind:checked={triggers.change_detection} />
        <span class="col" style="gap:2px">
          <strong>Detect changes in the image</strong>
          <span class="xsmall faint">Compares the region often and only runs the AI when it changed. No motion sensor needed.</span>
        </span>
      </label>
      {#if triggers.change_detection}
        <label class="line sub">
          <span class="small">Compare the region every</span>
          <span class="row"
            ><input
              class="input sm num"
              type="number"
              min={limits.change_interval_s[0]}
              max={limits.change_interval_s[1]}
              step="0.5"
              bind:value={triggers.change_interval_s}
            /> s</span
          >
        </label>
        <label class="line sub">
          <span class="col" style="gap:2px">
            <span class="small">React when this much of the region changes</span>
            <span class="xsmall faint">
              {#if liveScore !== null}Measured just now: <span class="mono">{pct(liveScore, 1)}</span> — keep the threshold above normal noise.{:else}Save and wait a moment to see the measured change here.{/if}
            </span>
          </span>
          <span class="row"
            ><input
              class="input sm num"
              type="number"
              min={limits.change_threshold[0] * 100}
              max={limits.change_threshold[1] * 100}
              step="0.5"
              value={+(triggers.change_threshold * 100).toFixed(1)}
              oninput={(e) => (triggers.change_threshold = Number(e.currentTarget.value) / 100)}
            /> %</span
          >
        </label>
      {/if}
    </div>

    <div class="block col" class:dim={!burstUsed}>
      <span class="col" style="gap:2px">
        <strong>After a trigger</strong>
        <span class="xsmall faint">Keep checking for a while to catch both the movement and the final state.</span>
      </span>
      <div class="row wrap small">
        <!-- Each value stays with its unit when the line wraps on a phone. -->
        <span class="row group">check every
        <input
          class="input sm num"
          type="number"
          min={limits.burst_interval_s[0]}
          max={limits.burst_interval_s[1]}
          step="0.5"
          bind:value={triggers.burst_interval_s}
          aria-label="Seconds between checks after a trigger"
        /> s</span>
        <span class="row group">for
        <input
          class="input sm num"
          type="number"
          min={limits.burst_duration_s[0]}
          max={limits.burst_duration_s[1]}
          step="5"
          bind:value={triggers.burst_duration_s}
          aria-label="How long to keep checking after a trigger"
        /> s</span>
      </div>
    </div>
  </div>
{/if}

<style>
  .group {
    gap: var(--space-2);
  }
  .triggers {
    gap: 0;
  }
  .block {
    padding: var(--space-3) 0;
    border-top: 1px solid var(--c-border);
  }
  .block:first-child {
    border-top: none;
    padding-top: 0;
  }
  .line {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-4);
    color: var(--c-text-2);
  }
  .sub {
    padding-left: 26px;
  }
  .check {
    align-items: flex-start;
  }
  .check input {
    margin-top: 3px;
  }
  .dim {
    opacity: 0.55;
  }
</style>
