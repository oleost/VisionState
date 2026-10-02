<script lang="ts">
  // "When to check": interval (can be off), trigger entities (optionally only one state of each)
  // with follow-up burst, optional change detection, and a light to switch on for each check.
  // Defaults and limits come from the backend (GET /config → trigger_defaults / trigger_limits).
  import { app } from '../app.svelte';
  import { pct } from '../format';
  import type { Triggers } from '../types';
  import EntityPicker from './EntityPicker.svelte';

  let {
    triggers = $bindable(),
    interval_s = $bindable(),
    liveScore = null,
    lightError = '',
  }: { triggers: Triggers; interval_s: number; liveScore?: number | null; lightError?: string } = $props();

  const limits = $derived(app.config?.trigger_limits);
  const intervalLimits = $derived(app.config?.sensor_limits.interval_s);
  const maxEntities = $derived(app.config?.trigger_max_entities ?? 20);
  const burstUsed = $derived(triggers.entities.length > 0 || triggers.change_detection);
  const lightDomains = $derived(app.config?.light_domains ?? []);
  // The picker works with a list; the light is at most one entity.
  let light = $state(triggers.light_entity ? [triggers.light_entity] : []);
  $effect(() => {
    triggers.light_entity = light[0] ?? '';
  });
</script>

{#if limits && intervalLimits}
  <div class="col triggers">
    <div class="block">
      <div class="line">
        <label class="check">
          <input type="checkbox" bind:checked={triggers.regular} aria-label="Regular check" />
          <span class="col" style="gap:2px">
            <strong>Regular check</strong>
            <span class="xsmall faint">
              {#if triggers.regular}
                The safety net, counted from the last check — so it rarely runs when triggers are frequent.
              {:else}
                Off: only checks when triggered, once after start-up and with the <em>check now</em> button in Home Assistant.
              {/if}
            </span>
          </span>
        </label>
        {#if triggers.regular}
          <span class="row"
            ><span class="small muted">every</span>
            <input
              class="input sm num"
              type="number"
              min={intervalLimits[0]}
              max={intervalLimits[1]}
              step="1"
              bind:value={interval_s}
              aria-label="Seconds between regular checks"
            /> s</span
          >
        {/if}
      </div>
    </div>

    <div class="block col">
      <span class="col" style="gap:2px">
        <strong>Check when these change</strong>
        <span class="xsmall faint">
          Motion sensors, door contacts, the garage opener… Any state change starts a check — or only one state,
          for example <span class="mono">on</span>.
        </span>
      </span>
      <EntityPicker bind:value={triggers.entities} bind:states={triggers.only_states} max={maxEntities} />
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

    <div class="block col">
      <span class="col" style="gap:2px">
        <strong>Switch on a light for each check</strong>
        <span class="xsmall faint">
          A lamp or the camera's flash, for a dark place. It is switched on before the frame is taken and off again
          afterwards; a light that is already on is left alone.
        </span>
      </span>
      <EntityPicker bind:value={light} max={1} domains={lightDomains} label="Light to switch on" placeholder="Search lights and switches" />
      {#if light.length}
        <label class="line sub">
          <span class="small">Wait before taking the frame</span>
          <span class="row"
            ><input
              class="input sm num"
              type="number"
              min={limits.light_delay_s[0]}
              max={limits.light_delay_s[1]}
              step="0.5"
              bind:value={triggers.light_delay_s}
            /> s</span
          >
        </label>
        <p class="xsmall faint sub">
          The live view shows the frame of the last check instead of taking new ones.{triggers.change_detection
            ? ' Change detection compares frames without the light.'
            : ''}
        </p>
        {#if lightError}<div class="notice warn small">Could not switch the light: {lightError}</div>{/if}
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
    display: flex;
    gap: var(--space-2);
    align-items: flex-start;
  }
  p.sub {
    margin: 0;
  }
  .check input {
    margin-top: 3px;
  }
  .dim {
    opacity: 0.55;
  }
</style>
