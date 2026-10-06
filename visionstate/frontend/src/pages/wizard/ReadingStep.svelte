<script lang="ts">
  // Wizard, "Detect" step of a reading sensor: what is read (display, digits, unit…), a test on a
  // fresh frame whose region can be fine-tuned right there, and its entity in Home Assistant.
  import { onMount, untrack } from 'svelte';
  import { api } from '../../lib/api';
  import DigitCells from '../../lib/components/DigitCells.svelte';
  import ReadingEditor from '../../lib/components/ReadingEditor.svelte';
  import RoiEditor from '../../lib/components/RoiEditor.svelte';
  import { pct } from '../../lib/format';
  import { digitsSeen, readingUnit } from '../../lib/reading';
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
        Read <span class="mono">“{result.text || '—'}”</span> — {digitsSeen(result.text)} digits, not {reading.digits}.
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
    <div class="read-images">
      <div class="col" style="gap:6px">
        <RoiEditor src={result.image} bind:roi editable>
          {#if reading.display === 'counter'}<DigitCells {roi} digits={reading.digits} />{/if}
        </RoiEditor>
        <span class="xsmall faint">
          {#if reading.display === 'counter'}
            Drag a box from the first wheel to the last, so each field holds one wheel. A little room above and below is
            fine — it is read again right away.
          {:else}
            Drag a tight box around the digits only — it is read again right away.
          {/if}
        </span>
      </div>
      <div class="col" style="gap:6px">
        <span class="xsmall faint">What the reader sees{reading.display === 'counter' ? ' — the wheels without their dividers' : ''}</span>
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
    .read-images {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
