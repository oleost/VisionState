<script lang="ts">
  import { api } from '../../lib/api';
  import { stateInfo, toast, toastError } from '../../lib/app.svelte';
  import { dateTime, pct } from '../../lib/format';
  import type { Prediction, Sensor } from '../../lib/types';
  import { href, paths } from '../../lib/router.svelte';
  import { REVIEW_REASONS } from '../../lib/ui';

  let { sensor }: { sensor: Sensor } = $props();

  let items = $state<Prediction[] | null>(null);
  let added = $state(new Map<number, string>()); // prediction id → state key it was added as
  let open = $state<number | null>(null); // prediction whose full frame is shown

  $effect(() => {
    void sensor.live.published;
    api
      .history(sensor.id)
      .then((r) => (items = r))
      .catch(toastError);
  });

  async function addAs(p: Prediction, key: string) {
    try {
      await api.answerReview(p.id, 'label', key);
      added = new Map([...added, [p.id, key]]);
      toast(`Added to dataset as ${stateInfo(sensor, key).name}`);
    } catch (err) {
      toastError(err);
    }
  }
</script>

<p class="small muted">
  State changes and frames flagged for review, kept as set under <a href={href(paths.settings())}>Settings → Storage</a>. Tap a frame to see it whole; use “Add as”
  to turn it into training data — especially when the AI got it wrong.
</p>

{#if items === null}
  <p class="muted">Loading…</p>
{:else if !items.length}
  <p class="muted">Nothing yet. The history fills up as the sensor runs.</p>
{:else}
  <div class="col list">
    {#each items as p (p.id)}
      {@const published = stateInfo(sensor, p.published_key)}
      {@const topInfo = stateInfo(sensor, p.state_key)}
      <div class="card item">
        {#if p.has_frame}
          <button class="thumb" onclick={() => (open = open === p.id ? null : p.id)} aria-expanded={open === p.id} aria-label="Show the whole frame">
            <img src={api.historyImageUrl(p.id, 'thumb')} alt="" loading="lazy" />
          </button>
        {/if}
        <div class="col what">
          <div class="row wrap" style="gap:8px">
            {#if p.is_change}
              <strong class="row" style="gap:8px"><span class="dot" style:background={published.color}></span>Changed to {published.name}</strong>
            {:else}
              <strong>AI saw {topInfo.name}</strong>
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
            {#if added.has(p.id)}
              <span class="small ok">Added as {stateInfo(sensor, added.get(p.id) ?? '').name} ✓</span>
            {:else}
              <span class="xsmall muted">Add as</span>
              {#each sensor.states as s (s.key)}
                <button class="btn sm state" style:border-left-color={s.color} onclick={() => addAs(p, s.key)}>{s.name}</button>
              {/each}
            {/if}
          </div>
        {/if}
        {#if open === p.id}
          <img class="full" src={api.historyImageUrl(p.id)} alt="Frame of {dateTime(p.created_at)}" />
        {/if}
      </div>
    {/each}
  </div>
{/if}

<style>
  .list {
    gap: var(--space-2);
  }
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
      flex-wrap: wrap;
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
