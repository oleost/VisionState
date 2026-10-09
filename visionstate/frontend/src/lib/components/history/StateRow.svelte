<script lang="ts">
  // A history row of a state sensor: a change or a frame flagged for review. The frame opens whole
  // and can be added to the dataset under any state.
  import { api } from '../../api';
  import { stateInfo, toast, toastError } from '../../app.svelte';
  import { dateTime, pct } from '../../format';
  import type { Prediction, Sensor } from '../../types';
  import { REVIEW_REASONS } from '../../ui';

  let { item: p, sensor, showSensor = false }: { item: Prediction; sensor: Sensor; showSensor?: boolean } = $props();

  let added = $state<string | null>(null); // the state this frame was added as
  let open = $state(false);

  const published = $derived(stateInfo(sensor, p.published_key));
  const top = $derived(stateInfo(sensor, p.state_key));

  async function addAs(key: string) {
    try {
      await api.answerReview(p.id, 'label', key);
      added = key;
      toast(`Added to dataset as ${stateInfo(sensor, key).name}`);
    } catch (err) {
      toastError(err);
    }
  }
</script>

<div class="card item">
  {#if p.has_frame}
    <button class="thumb" onclick={() => (open = !open)} aria-expanded={open} aria-label="Show the whole frame">
      <img src={api.historyImageUrl(p.id, 'thumb')} alt="" loading="lazy" />
    </button>
  {/if}
  <div class="col what">
    {#if showSensor}<span class="xsmall muted sensor">{sensor.name}</span>{/if}
    <div class="row wrap" style="gap:8px">
      {#if p.is_change}
        <strong class="row" style="gap:8px"><span class="dot" style:background={published.color}></span>Changed to {published.name}</strong>
      {:else}
        <strong>AI saw {top.name}</strong>
      {/if}
      {#if p.confidence}<span class="mono small muted">{pct(p.confidence)}</span>{/if}
      {#if p.review_reason}
        <span class="chip {REVIEW_REASONS[p.review_reason].tone}">{REVIEW_REASONS[p.review_reason].label}</span>
      {/if}
    </div>
    <span class="xsmall faint">{dateTime(p.created_at)}</span>
  </div>
  {#if p.has_frame}
    <div class="row wrap add">
      {#if added !== null}
        <span class="small ok">Added as {stateInfo(sensor, added).name} ✓</span>
      {:else}
        <span class="xsmall muted">Add as</span>
        {#each sensor.states as s (s.key)}
          <button class="btn sm state" style:border-left-color={s.color} onclick={() => addAs(s.key)}>{s.name}</button>
        {/each}
      {/if}
    </div>
  {/if}
  {#if open}
    <img class="full" src={api.historyImageUrl(p.id)} alt="Frame of {dateTime(p.created_at)}" />
  {/if}
</div>

<style>
  .item {
    display: flex;
    flex-wrap: wrap; /* the full frame, when open, takes its own row */
    align-items: center;
    gap: var(--space-4);
    padding: var(--space-2);
    padding-right: var(--space-4);
  }
  .what {
    gap: 4px;
    flex: 1 1 0; /* next to the frame, also on a phone; the chips wrap inside */
    min-width: 140px;
  }
  .sensor {
    overflow-wrap: anywhere;
  }
  .thumb {
    padding: 0;
    border: 0;
    background: none;
    cursor: zoom-in;
    line-height: 0;
  }
  .full {
    flex-basis: 100%;
    width: 100%;
    border-radius: var(--radius-sm);
  }
  .thumb img {
    width: 128px;
    aspect-ratio: 16 / 10;
    object-fit: cover;
    border-radius: var(--radius-sm);
  }
  .state {
    border-left-width: 4px;
  }
  .add {
    gap: var(--space-2);
  }
  .ok {
    color: var(--c-accent);
  }
  @media (max-width: 600px) {
    .item {
      gap: var(--space-3);
      padding: var(--space-3);
    }
    .thumb img {
      width: 96px;
    }
    .add {
      flex-basis: 100%; /* the "Add as" buttons get their own row under the frame */
    }
  }
</style>
