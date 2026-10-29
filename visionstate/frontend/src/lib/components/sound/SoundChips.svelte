<script lang="ts">
  // What a sound sensor hears right now: one chip per sound. `overlay` = one compact pill (dashboard).
  import { soundColor, soundSummary } from '../../sound';
  import type { Sensor } from '../../types';

  let { sensor, overlay = false }: { sensor: Sensor; overlay?: boolean } = $props();

  const live = $derived(sensor.sound?.live ?? []);
  const anyOn = $derived(live.some((s) => s.on));
</script>

{#if overlay}
  <span class="pill overlay">
    <span class="dot" style:background={anyOn ? 'var(--c-accent)' : 'var(--c-unknown)'}></span>
    {soundSummary(sensor)}
  </span>
{:else}
  <span class="row wrap chips">
    {#each live as s (s.key)}
      <span class="pill" class:off={!s.on}>
        <span class="dot" style:background={s.on ? soundColor(sensor, s.key) : 'var(--c-unknown)'}></span>
        {s.name}
      </span>
    {/each}
  </span>
{/if}

<style>
  .chips {
    gap: var(--space-2);
  }
  .pill {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    height: 28px;
    padding: 0 12px;
    border-radius: var(--radius-pill);
    background: var(--c-surface-3);
    font-size: var(--fs-md);
    font-weight: 600;
    white-space: nowrap;
  }
  .pill.off {
    color: var(--c-muted);
    font-weight: 500;
  }
  .pill.overlay {
    background: var(--c-overlay);
    height: 30px;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
  }
</style>
