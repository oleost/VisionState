<script lang="ts">
  import { api } from '../../lib/api';
  import { app, toast, toastError } from '../../lib/app.svelte';
  import Icon from '../../lib/components/Icon.svelte';
  import SampleGrid from '../../lib/components/SampleGrid.svelte';
  import type { SampleItem, Sensor } from '../../lib/types';

  let { sensor, onchange }: { sensor: Sensor; onchange: () => void } = $props();

  let frameInterval = $state(app.config?.video.frame_interval_s ?? 5);
  let useRoi = $state(true);
  let labelAll = $state('');
  let dragging = $state(false);
  let progress = $state<number | null>(null);
  let lastResult = $state('');
  let items = $state<SampleItem[]>([]);
  let input: HTMLInputElement;

  async function loadUnlabelled() {
    try {
      items = (await api.samples(sensor.id, { filter: 'unlabelled', limit: 500 })).items;
    } catch (err) {
      toastError(err);
    }
  }
  loadUnlabelled();

  async function send(files: File[]) {
    if (!files.length) return;
    progress = 0;
    try {
      const result = await api.upload(
        sensor.id,
        files,
        { stateKey: labelAll || null, frameIntervalS: frameInterval, useRoi },
        (f) => (progress = f),
      );
      lastResult = `${result.created} images added from ${files.length} file${files.length === 1 ? '' : 's'}`;
      toast(lastResult);
      result.errors.forEach((e) => toast(e, { tone: 'danger' }));
      await loadUnlabelled();
      onchange();
    } catch (err) {
      toastError(err);
    } finally {
      progress = null;
      input.value = '';
    }
  }

  function drop(e: DragEvent) {
    e.preventDefault();
    dragging = false;
    send(Array.from(e.dataTransfer?.files ?? []));
  }

  function refresh() {
    loadUnlabelled();
    onchange();
  }
</script>

<div class="top">
  <label
    class="drop"
    class:dragging
    ondragover={(e) => {
      e.preventDefault();
      dragging = true;
    }}
    ondragleave={() => (dragging = false)}
    ondrop={drop}
  >
    <input bind:this={input} type="file" multiple class="sr-only" accept="image/*,video/*,.zip" onchange={(e) => send(Array.from(e.currentTarget.files ?? []))} />
    <span class="icon"><Icon name="upload" size={22} /></span>
    {#if progress !== null}
      <span class="big">{progress < 1 ? `Uploading… ${Math.round(progress * 100)}%` : 'Processing frames…'}</span>
      <div class="progress"><div style:width="{progress * 100}%"></div></div>
    {:else}
      <span class="big">Drop images, ZIP archives or video here</span>
      <span class="small muted">or <span class="link">browse files</span> · JPG, PNG, WebP, ZIP, MP4, MKV, MOV</span>
    {/if}
    {#if lastResult && progress === null}<span class="small ok">{lastResult}</span>{/if}
  </label>

  <section class="card pad col">
    <h3>Import options</h3>
    <label class="field">
      Label everything as
      <select class="input sm" bind:value={labelAll}>
        <option value="">— review below —</option>
        {#each sensor.states as s (s.key)}<option value={s.key}>{s.name}</option>{/each}
      </select>
    </label>
    <label class="line small">
      <span>Video: one frame every</span>
      <span class="row" style="gap:6px"><input class="input sm num" type="number" min="0.2" step="0.5" bind:value={frameInterval} /> s</span>
    </label>
    <label class="check"><input type="checkbox" bind:checked={useRoi} /> Crop to the sensor region</label>
    <p class="xsmall faint">Untick for close-up photos that already show only the object.</p>
  </section>
</div>

<div class="row">
  <h2>Needs a label</h2>
  <span class="small muted">
    {items.length
      ? 'Click frames to select them, then pick a state (or press 1–9). Suggestions come from the current model.'
      : 'Nothing waiting. Upload files above, or label live frames on the Label tab.'}
  </span>
</div>

{#if items.length}
  <SampleGrid {sensor} {items} onchange={refresh} showSuggestions />
{/if}

<style>
  .top {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 360px;
    gap: var(--space-5);
  }
  .drop {
    border: 2px dashed var(--c-border-dashed);
    border-radius: var(--radius-xl);
    background: var(--c-sunken);
    padding: var(--space-6);
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 10px;
    text-align: center;
    cursor: pointer;
    min-height: 200px;
  }
  .drop.dragging {
    border-color: var(--c-accent);
    background: var(--c-accent-soft);
  }
  .icon {
    width: 44px;
    height: 44px;
    border-radius: 12px;
    background: var(--c-surface-2);
    color: var(--c-accent);
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .big {
    font: 600 18px var(--font-display);
  }
  .link {
    color: var(--c-accent);
    font-weight: 600;
  }
  .ok {
    color: var(--c-accent);
  }
  .progress {
    width: min(360px, 80%);
    height: 6px;
    border-radius: 3px;
    background: var(--c-surface-3);
    overflow: hidden;
  }
  .progress div {
    height: 100%;
    background: var(--c-accent);
    transition: width 0.2s;
  }
  .line {
    display: flex;
    align-items: center;
    justify-content: space-between;
    color: var(--c-text-2);
  }
  @media (max-width: 900px) {
    .top {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
