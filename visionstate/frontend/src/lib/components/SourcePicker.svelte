<script lang="ts">
  // Pick an image source: a Home Assistant camera, or a direct HTTP/RTSP URL.
  import { onMount } from 'svelte';
  import { api } from '../api';
  import { app } from '../app.svelte';
  import type { Camera } from '../types';

  let { sourceType = $bindable('ha_camera'), source = $bindable('') }: { sourceType: string; source: string } = $props();

  let cameras = $state<Camera[]>([]);
  let error = $state('');
  let loading = $state(true);
  let urlSource = $state(sourceType === 'ha_camera' ? '' : source);
  const urlTypes = $derived(Object.entries(app.config?.source_types ?? {}).filter(([k]) => k !== 'ha_camera'));

  onMount(async () => {
    try {
      cameras = await api.cameras();
      if (!source && cameras.length && sourceType === 'ha_camera') source = cameras[0].entity_id;
    } catch (err) {
      error = (err as Error).message;
    } finally {
      loading = false;
    }
  });

  function pickCamera(id: string) {
    sourceType = 'ha_camera';
    source = id;
  }

  function pickUrl(type: string) {
    sourceType = type;
    source = urlSource;
  }
</script>

<fieldset class="col">
  <legend class="field">Image source</legend>
  {#if loading}
    <p class="muted small">Loading cameras from Home Assistant…</p>
  {:else if error}
    <p class="notice warn">Could not list Home Assistant cameras: {error}</p>
  {:else if !cameras.length}
    <p class="muted small">No camera entities found in Home Assistant. Use a URL below instead.</p>
  {/if}

  <div class="cams">
    {#each cameras as cam (cam.entity_id)}
      <label class="cam" class:active={sourceType === 'ha_camera' && source === cam.entity_id}>
        <input
          type="radio"
          name="source"
          checked={sourceType === 'ha_camera' && source === cam.entity_id}
          onchange={() => pickCamera(cam.entity_id)}
        />
        <span class="col" style="gap:2px">
          <span>{cam.name}</span>
          <span class="mono xsmall muted">{cam.entity_id}</span>
        </span>
        {#if cam.state === 'unavailable'}<span class="chip danger">unavailable</span>{/if}
      </label>
    {/each}
  </div>

  <div class="url card pad col">
    <div class="row wrap">
      {#each urlTypes as [type, label] (type)}
        <label class="check">
          <input type="radio" name="source" checked={sourceType === type} onchange={() => pickUrl(type)} />
          {label}
        </label>
      {/each}
    </div>
    {#if sourceType !== 'ha_camera'}
      <input
        class="input"
        placeholder={sourceType === 'rtsp' ? 'rtsp://user:pass@192.168.1.20:554/stream1' : 'http://192.168.1.20/snapshot.jpg'}
        bind:value={urlSource}
        oninput={() => (source = urlSource)}
        aria-label="Source URL"
      />
    {/if}
  </div>
</fieldset>

<style>
  fieldset {
    border: none;
    margin: 0;
    padding: 0;
  }
  legend {
    padding: 0;
    margin-bottom: var(--space-2);
  }
  .cams {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: var(--space-3);
  }
  .cam {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-3);
    border-radius: var(--radius-lg);
    background: var(--c-bg);
    border: 1px solid var(--c-border);
    cursor: pointer;
  }
  .cam.active {
    border-color: var(--c-accent);
  }
  .cam input,
  .url input[type='radio'] {
    accent-color: var(--c-accent);
    width: 18px;
    height: 18px;
  }
  .url {
    background: var(--c-bg);
    border-style: dashed;
  }
</style>
