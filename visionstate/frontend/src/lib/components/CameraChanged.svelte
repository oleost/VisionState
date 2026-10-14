<script lang="ts">
  // A reading sensor whose camera came back with another picture size: its readings are held
  // until the size is back or the user says the region is still right (or draws it again).
  import { api } from '../api';
  import { toast, toastError } from '../app.svelte';
  import { ago } from '../format';
  import { href, paths } from '../router.svelte';
  import type { Sensor } from '../types';
  import Icon from './Icon.svelte';

  let { sensor, onSettings = false, onchange }: { sensor: Sensor; onSettings?: boolean; onchange: () => void } =
    $props();

  const changed = $derived(sensor.reading?.camera_changed);
  let busy = $state(false);

  async function accept() {
    busy = true;
    try {
      await api.acceptPicture(sensor.id);
      toast('Reading again');
      setTimeout(onchange, 800);
    } catch (err) {
      toastError(err);
    } finally {
      setTimeout(() => (busy = false), 800);
    }
  }
</script>

{#if changed}
  <div class="notice warn col changed" data-testid="camera-changed">
    <div class="row text">
      <Icon name="alert" />
      <span>
        <strong>The camera's picture changed</strong> from {changed.from[0]}×{changed.from[1]} to
        {changed.to[0]}×{changed.to[1]} pixels {ago(changed.since)}, so the region may show something else. Readings
        are held until the size is back, or until you check the region{onSettings ? ' below' : ''}.
      </span>
    </div>
    <div class="row wrap actions">
      <button class="btn sm" disabled={busy} onclick={accept}>
        <Icon name="check" size={14} /> The region is right
      </button>
      {#if !onSettings}
        <a class="btn sm" href={href(paths.sensor(sensor.id, 'settings'))}>Adjust the region</a>
      {/if}
    </div>
  </div>
{/if}

<style>
  .changed {
    gap: var(--space-3);
    align-items: stretch;
  }
  .text {
    align-items: flex-start;
    gap: var(--space-2);
  }
  .text > :global(svg) {
    flex-shrink: 0; /* the text wraps on phones; the icon keeps its size */
  }
  .actions {
    gap: var(--space-2);
  }
</style>
