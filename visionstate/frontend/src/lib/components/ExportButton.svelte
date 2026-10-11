<script lang="ts">
  // Export a sensor as a bundle to import elsewhere. A sensor where faces were taught asks first
  // whether to include them (unticked by default), with the names of their people.
  import { api } from '../api';
  import type { Sensor } from '../types';
  import Icon from './Icon.svelte';

  let { sensor }: { sensor: Sensor } = $props();

  const faces = $derived(sensor.objects?.taught_faces ?? 0);
  let open = $state(false);
  let withFaces = $state(false);
</script>

{#if !faces}
  <a class="btn sm" href={api.exportUrl(sensor.id)} download><Icon name="download" size={14} /> Export</a>
{:else}
  <span class="wrap">
    <button class="btn sm" onclick={() => (open = !open)} aria-expanded={open}>
      <Icon name="download" size={14} /> Export
    </button>
    {#if open}
      <div class="pop card pad col" role="dialog" aria-label="Export">
        <label class="check small">
          <input type="checkbox" bind:checked={withFaces} />
          Include the faces taught on this camera ({faces})
        </label>
        <span class="xsmall faint">With the names of their people; an import adds them to the people there.</span>
        <a class="btn sm primary" href={api.exportUrl(sensor.id, withFaces)} download onclick={() => (open = false)}>
          <Icon name="download" size={14} /> Download
        </a>
      </div>
    {/if}
  </span>
{/if}

<style>
  .wrap {
    position: relative;
    display: inline-flex;
  }
  .pop {
    position: absolute;
    right: 0;
    top: calc(100% + 6px);
    z-index: 30;
    width: min(280px, calc(100vw - 32px));
    gap: var(--space-2);
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
  }
  .pop .btn {
    align-self: flex-start;
  }
</style>
