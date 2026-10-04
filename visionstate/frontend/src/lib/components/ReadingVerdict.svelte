<script lang="ts">
  // Did the reader read the right number? Shown for a stored reading on the Quality tab and in
  // the review queue. The answer is kept with the reading: it shows how reliable the reading is
  // (the reader does not learn from it). The right value starts as what was read, so usually one
  // digit is changed; digits typed without a point are placed like the reader does, and the value
  // that will be saved is shown before it is.
  import { api } from '../api';
  import { toastError } from '../app.svelte';
  import { readingDetail, rightValue } from '../reading';
  import type { Prediction, ReadingSettings } from '../types';
  import Icon from './Icon.svelte';

  let {
    item,
    reading,
    unit = '',
    large = false,
    onanswer,
  }: {
    item: Prediction;
    /** The sensor's reading settings (where the decimal point goes). */
    reading: Pick<ReadingSettings, 'mode' | 'decimals'> | null;
    unit?: string;
    large?: boolean;
    /** Called after an answer was saved, with the new verdict. */
    onanswer?: (verdict: { read_ok: boolean | null; correct_value: string | null }) => void;
  } = $props();

  let asking = $state(false);
  let value = $state('');
  let busy = $state(false);
  let changing = $state(false);

  const saved = $derived(reading ? rightValue(value, reading) : value.trim() || null);
  const invalid = $derived(!!value.trim() && saved === null);

  function ask() {
    value = item.correct_value ?? readingDetail(item).value ?? '';
    asking = true;
  }

  async function send(action: 'read_ok' | 'misread' | 'skip') {
    busy = true;
    try {
      await api.verifyReading(item.id, action, action === 'misread' ? value.trim() : undefined);
      const verdict =
        action === 'skip'
          ? { read_ok: item.read_ok, correct_value: item.correct_value }
          : { read_ok: action === 'read_ok', correct_value: action === 'misread' ? saved : null };
      asking = false;
      changing = false;
      onanswer?.(verdict);
    } catch (err) {
      toastError(err);
    } finally {
      busy = false;
    }
  }
</script>

{#if item.read_ok !== null && !changing}
  <span class="row wrap verdict">
    {#if item.read_ok}
      <span class="chip ok"><Icon name="check" size={12} /> Read correctly</span>
    {:else}
      <span class="chip danger">Misread{item.correct_value ? ` — was ${item.correct_value}${unit ? ` ${unit}` : ''}` : ''}</span>
    {/if}
    <button type="button" class="linkish xsmall" onclick={() => (changing = true)}>Change</button>
  </span>
{:else if asking}
  <form
    class="row wrap verdict"
    onsubmit={(e) => {
      e.preventDefault();
      send('misread');
    }}
  >
    <span class="col" style="gap:2px">
      <label class="row value">
        <span class="small muted">It showed</span>
        <input class="input sm mono" bind:value inputmode="decimal" placeholder="optional" aria-label="The right value" />
        {#if unit}<span class="small muted">{unit}</span>{/if}
      </label>
      <span class="xsmall preview" class:bad={invalid} aria-live="polite">
        {#if invalid}Not a number{:else if saved}Saved as <span class="mono">{saved}{unit ? ` ${unit}` : ''}</span>{:else}Leave empty if you don't know{/if}
      </span>
    </span>
    <button class="btn sm primary" class:lg={large} disabled={busy || invalid}>Save misread</button>
    <button type="button" class="btn sm ghost" class:lg={large} disabled={busy} onclick={() => (asking = false)}>Cancel</button>
  </form>
{:else}
  <span class="row wrap verdict">
    <button type="button" class="btn sm" class:lg={large} disabled={busy} onclick={() => send('read_ok')}>
      <Icon name="check" size={14} /> Read correctly
    </button>
    <button type="button" class="btn sm" class:lg={large} disabled={busy} onclick={ask}>Misread</button>
    {#if large}<button type="button" class="btn lg ghost" disabled={busy} onclick={() => send('skip')}>Skip</button>{/if}
  </span>
{/if}

<style>
  .verdict {
    gap: var(--space-2);
    align-items: center;
  }
  .value {
    gap: var(--space-2);
  }
  .value .input {
    width: 130px;
  }
  .preview {
    color: var(--c-faint);
  }
  .preview.bad {
    color: var(--c-danger-text);
  }
  .linkish {
    background: none;
    border: none;
    padding: 0;
    color: var(--c-accent);
    font: inherit;
    cursor: pointer;
  }
</style>
