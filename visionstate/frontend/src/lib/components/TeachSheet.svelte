<script lang="ts">
  // Teach an object sensor about one box: right, not what the AI said, something else (one of its
  // objects or own labels, or a new own label), or — for a box the user drew — what the AI missed.
  // The first time a sensor is taught, it asks once whether to start. A bottom sheet on phones,
  // a small dialog on wider screens.
  import { onMount, untrack } from 'svelte';
  import { api } from '../api';
  import { app, toast, toastError } from '../app.svelte';
  import { pct } from '../format';
  import { activeLabels, boxName, boxReason, noneLabel, objectName } from '../objects';
  import type { Detection, Sensor, TeachInput } from '../types';
  import Icon from './Icon.svelte';

  let {
    sensor,
    detection,
    box,
    source,
    src,
    onclose,
    ondone,
  }: {
    sensor: Sensor;
    /** The tapped box; null for a box the user drew around something the AI missed. */
    detection: Detection | null;
    box: [number, number, number, number];
    /** The frame the box is on: the one a check analysed, or a history frame. */
    source: { frame_id: string } | { history_id: number };
    /** That frame, for the close-up. */
    src: string | null;
    onclose: () => void;
    ondone: () => void;
  } = $props();

  type View = 'main' | 'other' | 'new' | 'confirm';
  let view = $state<View>(untrack(() => detection) ? 'main' : 'other'); // the sheet is made for one box
  let pending = $state<{ label?: string; new_label?: string; summary: string } | null>(null);
  let newName = $state('');
  let busy = $state(false);
  let panel: HTMLDivElement;

  const own = $derived(sensor.objects?.custom ?? []);
  const classes = $derived(sensor.objects?.classes ?? []);
  const current = $derived(detection ? (detection.label ?? detection.key) : null);
  const name = $derived(detection ? boxName(detection, own) : '');
  const objectWord = $derived(detection ? objectName(detection.key).toLowerCase() : '');
  const firstTime = $derived((sensor.objects?.taught ?? 0) === 0);
  const roomForLabel = $derived(own.length < (app.config?.teach.max_labels ?? 0));
  // Everything the box could be instead: the sensor's objects and own labels, except what it is now.
  const options = $derived([
    ...classes.filter((k) => k !== current).map((k) => ({ key: k, text: objectName(k), sub: '' })),
    ...activeLabels(sensor)
      .filter((l) => l.key !== current)
      .map((l) => ({ key: l.key, text: l.name, sub: `a kind of ${objectName(l.parent).toLowerCase()}` })),
  ]);

  // Close-up of the box with some of its surroundings (needs the image's size for the shape).
  let natural = $state<{ w: number; h: number } | null>(null);
  $effect(() => {
    if (!src) return;
    const img = new Image();
    img.onload = () => (natural = { w: img.naturalWidth, h: img.naturalHeight });
    img.src = src;
  });
  const area = $derived.by(() => {
    const [x1, y1, x2, y2] = box;
    const mx = (x2 - x1) * 0.2;
    const my = (y2 - y1) * 0.2;
    return [Math.max(0, x1 - mx), Math.max(0, y1 - my), Math.min(1, x2 + mx), Math.min(1, y2 + my)];
  });
  const crop = $derived.by(() => {
    if (!natural) return null;
    const [x1, y1, x2, y2] = area;
    const w = x2 - x1;
    const h = y2 - y1;
    return {
      aspect: (w * natural.w) / (h * natural.h),
      size: `${100 / w}% ${100 / h}%`,
      position: `${w < 1 ? (x1 / (1 - w)) * 100 : 0}% ${h < 1 ? (y1 / (1 - h)) * 100 : 0}%`,
    };
  });

  onMount(() => {
    panel?.querySelector<HTMLElement>('button.first')?.focus();
  });

  function choose(choice: { label?: string; new_label?: string }, summary: string) {
    pending = { ...choice, summary };
    if (firstTime) view = 'confirm';
    else send();
  }

  async function send() {
    if (!pending) return;
    busy = true;
    const body: TeachInput = {
      ...source,
      box,
      detected: detection ? (detection.was ?? detection.key) : null,
      score: detection?.score ?? null,
      ...(pending.new_label ? { new_label: pending.new_label, parent: detection?.key } : { label: pending.label }),
    };
    try {
      const result = await api.teachBox(sensor.id, body);
      if (result.seen === false) {
        toast(`${pending.summary} The AI sees nothing at all there yet, so it cannot find this by itself.`, { tone: 'warn' });
      } else toast(pending.summary);
      ondone();
      onclose();
    } catch (err) {
      toastError(err);
      busy = false;
    }
  }

  function keydown(e: KeyboardEvent) {
    if (e.key === 'Escape') onclose();
  }
</script>

<svelte:window onkeydown={keydown} />

<div class="backdrop" onclick={onclose} role="presentation"></div>
<div class="sheet" role="dialog" aria-modal="true" aria-label="Teach this box" bind:this={panel}>
  <div class="head row">
    {#if crop}
      <div
        class="closeup"
        style:aspect-ratio={crop.aspect}
        style:background-image="url({src})"
        style:background-size={crop.size}
        style:background-position={crop.position}
      ></div>
    {/if}
    <div class="col" style="gap:4px;min-width:0;flex:1">
      {#if detection}
        <strong>{name} <span class="mono small muted">{pct(detection.score)} sure</span></strong>
        {#if boxReason(detection, own)}<span class="xsmall muted">{boxReason(detection, own)}</span>{/if}
      {:else}
        <strong>What did the AI miss?</strong>
        <span class="xsmall muted">It will look closer at boxes like this it is unsure about.</span>
      {/if}
    </div>
    <button class="btn ghost icon close" onclick={onclose} aria-label="Close"><Icon name="x" /></button>
  </div>

  {#if view === 'confirm' && pending}
    <div class="col body">
      <strong>Teach this sensor?</strong>
      <p class="small muted">
        {#if detection}
          VisionState will check each {objectWord} it finds against what you taught.
        {:else}
          VisionState will look again at boxes it is unsure about and compare them with what you showed it.
        {/if}
        You can turn this off or forget it under Settings.
      </p>
      <div class="row actions">
        <button class="btn primary first" disabled={busy} onclick={send}>Teach</button>
        <button class="btn" onclick={onclose}>Cancel</button>
      </div>
    </div>
  {:else if view === 'main' && detection}
    <div class="col body">
      {#if detection.filtered}
        <button class="btn first choice" disabled={busy} onclick={() => choose({ label: detection.key }, `Got it: boxes like this count as ${objectWord} again.`)}>
          <Icon name="check" /> It is a {objectWord}
        </button>
      {:else}
        <button class="btn first choice" disabled={busy} onclick={() => choose({ label: current! }, `Got it: ${name} is right.`)}>
          <Icon name="check" /> Correct
        </button>
        <button
          class="btn choice"
          disabled={busy}
          onclick={() => choose({ label: noneLabel() }, `Got it: boxes like this no longer count as ${objectWord}.`)}
        >
          <Icon name="x" /> Not a {objectWord}
        </button>
      {/if}
      <button class="btn choice" disabled={busy} onclick={() => (view = 'other')}>
        <Icon name="tag" /> Something else…
      </button>
    </div>
  {:else if view === 'other'}
    <div class="col body">
      {#if detection}<span class="small muted">It is …</span>{/if}
      {#each options as o, i (o.key)}
        <button
          class="btn choice"
          class:first={i === 0}
          disabled={busy}
          onclick={() => choose({ label: o.key }, detection ? `Got it: boxes like this count as ${o.text}.` : `Saved as ${o.text}.`)}
        >
          {o.text}{#if o.sub}<span class="xsmall faint">{o.sub}</span>{/if}
        </button>
      {/each}
      {#if detection && roomForLabel}
        <button class="btn choice accent-outline" disabled={busy} onclick={() => (view = 'new')}>
          <Icon name="plus" /> New label…
        </button>
      {/if}
      {#if detection}<button class="btn ghost sm back" onclick={() => (view = 'main')}><Icon name="back" size={14} /> Back</button>{/if}
    </div>
  {:else if view === 'new' && detection}
    <form
      class="col body"
      onsubmit={(e) => {
        e.preventDefault();
        const label = newName.trim();
        if (label) choose({ new_label: label }, `Added ${label}. It gets its own entities in Home Assistant.`);
      }}
    >
      <label class="field">
        A kind of {objectWord}, for example “Our {objectWord}”
        <!-- svelte-ignore a11y_autofocus -->
        <input class="input" bind:value={newName} maxlength="64" placeholder="Name" autofocus />
      </label>
      <span class="xsmall faint">It gets an on/off sensor and a count in Home Assistant, like the objects do.</span>
      <div class="row actions">
        <button class="btn primary" type="submit" disabled={busy || !newName.trim()}>Add label</button>
        <button class="btn" type="button" onclick={() => (view = 'other')}>Back</button>
      </div>
    </form>
  {/if}
</div>

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    z-index: 40;
    background: rgba(5, 7, 9, 0.6);
  }
  .sheet {
    position: fixed;
    z-index: 41;
    left: 50%;
    top: 50%;
    transform: translate(-50%, -50%);
    width: min(420px, calc(100vw - 32px));
    max-height: calc(100vh - 48px);
    overflow-y: auto;
    background: var(--c-surface);
    border: 1px solid var(--c-border-strong);
    border-radius: var(--radius-lg);
    padding: var(--space-4);
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
  }
  @media (max-width: 600px) {
    .sheet {
      left: 0;
      right: 0;
      top: auto;
      bottom: 0;
      transform: none;
      width: auto;
      max-height: 85vh;
      border-radius: var(--radius-lg) var(--radius-lg) 0 0;
      padding-bottom: calc(var(--space-4) + env(safe-area-inset-bottom));
    }
  }
  .head {
    gap: var(--space-3);
    align-items: flex-start;
  }
  .closeup {
    width: 72px;
    max-height: 120px;
    flex-shrink: 0;
    border-radius: var(--radius-sm);
    background-repeat: no-repeat;
    background-color: var(--c-sunken);
  }
  .close {
    flex-shrink: 0;
    margin: -6px -6px 0 0;
  }
  .body {
    gap: var(--space-2);
  }
  .choice {
    height: var(--touch);
    justify-content: flex-start;
    gap: var(--space-2);
  }
  .choice .xsmall {
    margin-left: auto;
  }
  .back {
    align-self: flex-start;
  }
  .actions {
    gap: var(--space-2);
    margin-top: var(--space-2);
  }
  p {
    margin: 0;
  }
</style>
