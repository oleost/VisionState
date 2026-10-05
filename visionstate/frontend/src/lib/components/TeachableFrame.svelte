<script lang="ts">
  // A frame of an object sensor with its boxes, where every box can be corrected (tap it, or its
  // entry under the frame) and a box can be drawn around something the AI missed. Used on the
  // Live tab (the frame the last check analysed) and for history frames. While a box is being
  // taught, `busy` is set so the owner keeps this frame on screen.
  import { activeLabels, boxName, objectColor, objectKeys } from '../objects';
  import { pct } from '../format';
  import type { Detection, Sensor } from '../types';
  import BoxDraw from './BoxDraw.svelte';
  import DetectionBoxes from './DetectionBoxes.svelte';
  import Icon from './Icon.svelte';
  import RoiEditor from './RoiEditor.svelte';
  import TeachSheet from './TeachSheet.svelte';

  let {
    sensor,
    src,
    detections,
    source,
    busy = $bindable(false),
    onchange,
  }: {
    sensor: Sensor;
    src: string | null;
    detections: Detection[];
    /** The frame shown, as the backend knows it; null while there is none to teach on. */
    source: { frame_id: string } | { history_id: number } | null;
    busy?: boolean;
    onchange: () => void;
  } = $props();

  let picked = $state<number | null>(null);
  let drawing = $state(false);
  let drawn = $state<[number, number, number, number] | null>(null);
  let missed = $state(false); // the sheet for the drawn box is open

  const own = $derived(sensor.objects?.custom ?? []);
  const keys = $derived(objectKeys(sensor));
  const taught = $derived((sensor.objects?.taught ?? 0) > 0);
  // Counted boxes first, most certain first; filtered ones last.
  const order = $derived(
    detections
      .map((d, i) => ({ d, i }))
      .sort((a, b) => Number(!!a.d.filtered) - Number(!!b.d.filtered) || b.d.score - a.d.score),
  );

  $effect(() => {
    busy = picked !== null || drawing || missed;
  });

  function startDrawing() {
    picked = null;
    drawn = null;
    drawing = true;
  }

  function close() {
    picked = null;
    missed = false;
    drawing = false;
    drawn = null;
  }
</script>

<RoiEditor {src} roi={sensor.roi}>
  {#if drawing}
    <BoxDraw bind:box={drawn} />
  {:else}
    <DetectionBoxes {detections} classes={keys} {own} selected={picked} onpick={source ? (i) => (picked = i) : undefined} />
  {/if}
</RoiEditor>

<div class="teachbar row wrap">
  {#if drawing}
    <span class="small">Drag a box around what the AI missed.</span>
    <span class="spacer"></span>
    <span class="row" style="gap:8px">
      <button class="btn sm" onclick={close}>Cancel</button>
      <button class="btn sm primary" disabled={!drawn} onclick={() => (missed = true)}>Next</button>
    </span>
  {:else}
    {#if detections.length && source}
      <span class="xsmall faint">{taught ? 'In this frame:' : 'Wrong? Tap a box to correct it:'}</span>
      {#each order as { d, i } (i)}
        <button class="found" class:filtered={d.filtered} onclick={() => (picked = i)}>
          <span class="dot" style:background={d.filtered ? 'var(--c-unknown)' : objectColor(keys, d.label ?? d.key)}></span>
          {boxName(d, own)}
          <span class="mono faint">{d.filtered ? 'filtered' : pct(d.score)}</span>
        </button>
      {/each}
    {/if}
    <span class="spacer"></span>
    {#if source}
      <button class="btn sm ghost missed" onclick={startDrawing}><Icon name="draw" size={14} /> Missed something?</button>
    {/if}
  {/if}
</div>

{#if source && picked !== null && detections[picked]}
  <TeachSheet {sensor} {src} {source} detection={detections[picked]} box={detections[picked].box} onclose={close} ondone={onchange} />
{:else if source && missed && drawn && (activeLabels(sensor).length || sensor.objects?.classes.length)}
  <TeachSheet {sensor} {src} {source} detection={null} box={drawn} onclose={close} ondone={onchange} />
{/if}

<style>
  .teachbar {
    gap: 6px var(--space-2);
    padding: 10px 14px;
    border-bottom: 1px solid var(--c-border);
    align-items: center;
  }
  .found {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    min-height: 30px;
    padding: 0 10px;
    border-radius: var(--radius-pill);
    border: 1px solid var(--c-border-strong);
    background: var(--c-surface-2);
    color: var(--c-text);
    font: 500 var(--fs-sm) var(--font-body);
    cursor: pointer;
    white-space: nowrap;
  }
  .found:hover {
    background: var(--c-surface-3);
  }
  .found.filtered {
    border-style: dashed;
    color: var(--c-muted);
  }
  .missed {
    color: var(--c-text-2);
  }
</style>
