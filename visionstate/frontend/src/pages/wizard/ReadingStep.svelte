<script lang="ts">
  // Wizard, "Detect" step of a reading sensor: what is read (display, digits, unit…), a test on a
  // fresh frame whose region can be fine-tuned right there, and its entity in Home Assistant.
  import { onMount, untrack } from 'svelte';
  import { api } from '../../lib/api';
  import DialsEditor from '../../lib/components/DialsEditor.svelte';
  import DigitCells from '../../lib/components/DigitCells.svelte';
  import Icon from '../../lib/components/Icon.svelte';
  import ReadingEditor from '../../lib/components/ReadingEditor.svelte';
  import RoiEditor from '../../lib/components/RoiEditor.svelte';
  import StraightenSwitch from '../../lib/components/StraightenSwitch.svelte';
  import { pct } from '../../lib/format';
  import { digitsSeen, extraDigits, readingUnit } from '../../lib/reading';
  import { isPolygon, toPoints, toRectangle, turnTop } from '../../lib/roi';
  import type { ReadPreview, ReadingSettings, Roi } from '../../lib/types';
  import HaPreview from './HaPreview.svelte';
  import TestCard from './TestCard.svelte';

  let {
    sourceType,
    source,
    entity,
    roi = $bindable(),
    reading = $bindable(),
  }: { sourceType: string; source: string; entity: string; roi: Roi | null; reading: ReadingSettings } = $props();

  // The frame, what the reader saw, and what it read.
  let result = $state<ReadPreview | null>(null);
  let error = $state('');
  let busy = $state(false);
  // A counter read by a wheel reader can also have pointer dials: the picture edits one or the other.
  let editing = $state<'counter' | 'dials'>('counter');
  const canDial = $derived(reading.display === 'counter' && reading.counter_reader === 'wheels');
  const digitsRead = $derived(reading.digits + extraDigits(reading));

  /** Read a fresh frame with the current region and settings (also called when the light is on). */
  export async function test() {
    busy = true;
    error = '';
    try {
      result = await api.previewRead(sourceType, source, roi, reading);
    } catch (err) {
      error = (err as Error).message;
    } finally {
      busy = false;
    }
  }

  onMount(() => {
    test();
  });

  // A straightened region has four corners: a polygon of more becomes its bounding box.
  $effect(() => {
    if (reading.straighten && roi && toPoints(roi).length > 4) roi = toRectangle(roi);
  });

  // Read again when the region or the settings change (the frame is editable), so what is shown
  // always matches the current choices.
  let retestTimer: ReturnType<typeof setTimeout> | undefined;
  $effect(() => {
    const snapshot = JSON.stringify([roi, reading]);
    // The result is read untracked: the test updates it and must not re-trigger this.
    if (!untrack(() => result)) return;
    clearTimeout(retestTimer);
    retestTimer = setTimeout(() => snapshot && test(), 500);
    return () => clearTimeout(retestTimer);
  });
</script>

<div class="col" style="gap:var(--space-3)">
  <h3>What are you reading?</h3>
  <ReadingEditor bind:value={reading} seen={result?.text} />
</div>
<TestCard {busy} onretest={test}>
  {#snippet status()}
    <!-- While re-testing, the last result stays so the page does not jump. -->
    {#if busy && !result}
      <span class="muted">Reading… the first time loads the reader, which takes a moment.</span>
    {:else if error}
      <span class="danger-text">{error}</span>
    {:else if result?.wrong_digit_count}
      <span class="danger-text">
        Read <span class="mono">“{result.text || '—'}”</span> — {digitsSeen(result.text)} digits, not {digitsRead}.
      </span>
      <span class="muted">Make the box cover exactly the {reading.digits} wheels, or change the number of digits.</span>
    {:else if result?.value}
      Read <span class="mono">“{result.text}”</span> →
      <strong class="mono">{result.value} {readingUnit(reading)}</strong> · {pct(result.score)} sure
    {:else if result}
      <span class="muted">No number found. Drag a tight box around the digits in the image below.</span>
    {/if}
    {#if busy && result}<span class="faint"> (reading…)</span>{/if}
  {/snippet}
  {#if result}
    {#if canDial}
      <div class="row wrap edit-switch" role="group" aria-label="What the picture edits">
        <span class="small muted">Edit:</span>
        <button class="btn sm" class:primary={editing === 'counter'} aria-pressed={editing === 'counter'} onclick={() => (editing = 'counter')}>
          Counter
        </button>
        <button class="btn sm" class:primary={editing === 'dials'} aria-pressed={editing === 'dials'} onclick={() => (editing = 'dials')} data-testid="edit-dials">
          Dials{reading.dials.length ? ` (${reading.dials.length})` : ''}
        </button>
        {#if editing === 'counter' && !reading.dials.length}
          <span class="xsmall faint">Has the meter pointer dials (×0.1, ×0.01 …)? Add them under <em>Dials</em>.</span>
        {/if}
      </div>
    {/if}
    <div class="read-images">
      {#if canDial && editing === 'dials'}
        <DialsEditor preview={result} bind:dials={reading.dials} decimals={reading.decimals} />
      {:else}
      <div class="col" style="gap:6px">
        <RoiEditor src={result.image} bind:roi editable quad={reading.straighten}>
          {#if reading.display === 'counter' && !(reading.straighten && isPolygon(roi))}
            <DigitCells {roi} digits={reading.digits} whole={reading.counter_reader === 'wheels'} />
          {/if}
        </RoiEditor>
        <StraightenSwitch bind:checked={reading.straighten} counter={reading.display === 'counter'} />
        {#if reading.straighten && roi}
          <div class="row wrap" style="gap:8px">
            <button class="btn sm" onclick={() => roi && (roi = turnTop(roi))} title="The edge clockwise from it becomes the top" data-testid="roi-turn">
              <Icon name="refresh" size={14} /> Turn
            </button>
          </div>
        {/if}
        <span class="xsmall faint">
          {#if reading.straighten}
            Put the corners on the corners of the {reading.display === 'counter' ? 'wheels' : 'digits'}; see on the right
            how the reader sees it, upright.
          {:else if reading.display === 'counter'}
            Drag a box from the first wheel to the last, so each field holds one wheel. A little room above and below is
            fine — it is read again right away.
          {:else}
            Drag a tight box around the digits only — it is read again right away.
          {/if}
        </span>
      </div>
      {/if}
      <div class="col" style="gap:6px">
        <span class="xsmall faint">What the reader sees{reading.display === 'counter'
            ? reading.counter_reader === 'wheels'
              ? ` — one field per wheel${reading.dials.length ? ', then the dials' : ''}`
              : ' — the wheels without their dividers'
            : ''}</span>
        <img class="seen" src={result.read_image} alt="The region as the number reader saw it" />
      </div>
    </div>
  {/if}
</TestCard>
<HaPreview>
  <span class="mono">sensor.{entity}</span>
  <span class="mono xsmall muted">
    {reading.mode === 'counter' ? 'state_class: total_increasing' : 'state_class: measurement'}{readingUnit(reading)
      ? ` · unit: ${readingUnit(reading)}`
      : ''}
  </span>
</HaPreview>

<style>
  .danger-text {
    color: var(--c-danger);
  }
  .edit-switch {
    gap: 8px;
    padding: 0 16px 12px;
  }
  .read-images {
    display: grid;
    grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
    gap: var(--space-3);
    padding: 0 16px 16px;
    align-items: start;
  }
  .seen {
    max-width: 100%;
    border-radius: var(--radius-sm);
    border: 1px solid var(--c-border);
  }
  @media (max-width: 600px) {
    .edit-switch {
    gap: 8px;
    padding: 0 16px 12px;
  }
  .read-images {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
