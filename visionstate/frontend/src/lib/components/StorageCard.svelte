<script lang="ts">
  // Disk use and the history limits (days and size; whichever is reached first applies).
  // Limits come from the backend config.
  import { api } from '../api';
  import { app, toast, toastError } from '../app.svelte';
  import { ago, plural, size } from '../format';
  import type { StorageInfo } from '../types';

  let info = $state<StorageInfo | null>(null);
  let days = $state(7);
  let maxGb = $state(2);
  let saving = $state(false);

  const limits = $derived(app.config?.storage_limits);
  const changed = $derived(info !== null && (days !== info.history_days || maxGb !== info.history_max_gb));

  function show(s: StorageInfo) {
    info = s;
    days = s.history_days;
    maxGb = s.history_max_gb;
  }

  api.storage().then(show).catch(toastError);

  async function save() {
    saving = true;
    try {
      show(await api.saveStorage({ history_days: days, history_max_gb: maxGb }));
      toast('Storage limits saved');
    } catch (err) {
      toastError(err);
    } finally {
      saving = false;
    }
  }
</script>

<section class="card pad col">
  <h3>Storage</h3>
  {#if info && limits}
    <div class="col usage">
      <div class="row small">
        <span class="muted label">History frames</span>
        <strong>{size(info.history_bytes)}</strong>
        <span class="faint">
          {plural(info.history_frames, 'frame')}{info.oldest_history ? ` · oldest ${ago(info.oldest_history)}` : ''}
        </span>
      </div>
      <div class="row small">
        <span class="muted label">Training images</span>
        <strong>{size(info.training_bytes)}</strong>
        <span class="faint">{plural(info.training_images, 'image')} · never removed automatically</span>
      </div>
      <div class="row small">
        <span class="muted label">Free in the media folder</span>
        <strong>{size(info.free_bytes)}</strong>
      </div>
    </div>

    <label class="line">
      <span>Keep history for</span>
      <span class="row"
        ><input class="input sm num" type="number" min={limits.history_days[0]} max={limits.history_days[1]} step="1" bind:value={days} />
        <span class="unit small muted">days</span></span
      >
    </label>
    <label class="line">
      <span>… but at most (0 = no limit)</span>
      <span class="row"
        ><input class="input sm num" type="number" min={limits.history_max_gb[0]} max={limits.history_max_gb[1]} step="0.5" bind:value={maxGb} />
        <span class="unit small muted">GB</span></span
      >
    </label>
    <p class="xsmall muted">
      Whichever is reached first applies: the oldest history frames are removed first, frames waiting for review last.
      Frames waiting for review are kept twice as many days.
    </p>
    {#if info.limited_by_size}
      <div class="notice warn small">
        History is limited by size: older frames were removed before {info.history_days} days had passed. Raise the size
        limit to keep more.
      </div>
    {/if}
    <button class="btn primary" style="align-self:flex-start" disabled={!changed || saving} onclick={save}>
      {saving ? 'Saving…' : 'Save storage limits'}
    </button>
  {:else}
    <p class="small muted">Loading…</p>
  {/if}
</section>

<style>
  .usage {
    gap: var(--space-2);
  }
  .usage .row {
    gap: var(--space-3);
    flex-wrap: wrap;
  }
  .label {
    width: 170px;
  }
  .unit {
    width: 36px;
  }
  .line {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-4);
    font-size: var(--fs-base);
    color: var(--c-text-2);
  }
  @media (max-width: 600px) {
    .label {
      width: auto;
      flex-basis: 100%;
    }
  }
</style>
