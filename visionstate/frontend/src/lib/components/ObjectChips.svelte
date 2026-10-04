<script lang="ts">
  // What an object sensor reports right now: one chip per class (count when detected).
  // `overlay` = one compact pill for use on top of an image (dashboard).
  import { objectColor, objectKeys, objectName, objectSummary } from '../objects';
  import type { Sensor } from '../types';

  let { sensor, overlay = false }: { sensor: Sensor; overlay?: boolean } = $props();

  const classes = $derived(objectKeys(sensor));
  const own = $derived(sensor.objects?.custom ?? []);
  const live = $derived(sensor.objects?.live ?? []);
  const anyOn = $derived(live.some((o) => o.on));
</script>

{#if overlay}
  <span class="pill overlay">
    <span class="dot" style:background={anyOn ? 'var(--c-accent)' : 'var(--c-unknown)'}></span>
    {objectSummary(sensor)}
  </span>
{:else}
  <span class="row wrap chips">
    {#each live as o (o.key)}
      <span class="pill" class:off={!o.on}>
        <span class="dot" style:background={o.on ? objectColor(classes, o.key) : 'var(--c-unknown)'}></span>
        {objectName(o.key, own)}
        <span class="count mono">{o.on ? o.count : 0}</span>
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
  .count {
    font-weight: 400;
    font-size: var(--fs-sm);
    color: var(--c-text-2);
  }
</style>
