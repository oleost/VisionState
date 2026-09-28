<script lang="ts">
  import { api } from '../../lib/api';
  import { stateInfo, toast, toastError } from '../../lib/app.svelte';
  import { dateTime, pct } from '../../lib/format';
  import type { Prediction, Sensor } from '../../lib/types';
  import { REVIEW_REASONS } from '../../lib/ui';

  let { sensor }: { sensor: Sensor } = $props();

  let items = $state<Prediction[] | null>(null);
  let added = $state(new Set<number>());

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
      added = new Set([...added, p.id]);
      toast(`Added to dataset as ${stateInfo(sensor, key).name}`);
    } catch (err) {
      toastError(err);
    }
  }
</script>

<p class="small muted">
  State changes and frames flagged for review, kept for the retention period set in the app options. Use “Add as” to
  turn a frame into training data — especially when the AI got it wrong.
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
        {#if p.has_frame}<img src={api.historyImageUrl(p.id, 'thumb')} alt="" loading="lazy" />{/if}
        <div class="col" style="gap:4px;flex-grow:1;min-width:0">
          <div class="row wrap" style="gap:8px">
            {#if p.is_change}
              <strong class="row" style="gap:8px"><span class="dot" style:background={published.color}></span>Changed to {published.name}</strong>
            {:else}
              <strong>AI saw {topInfo.name}</strong>
            {/if}
            <span class="mono small muted">{pct(p.confidence)}</span>
            {#if p.review_reason}
              <span class="chip {REVIEW_REASONS[p.review_reason].tone}">{REVIEW_REASONS[p.review_reason].label}</span>
            {/if}
          </div>
          <span class="xsmall faint">{dateTime(p.created_at)}</span>
        </div>
        {#if p.has_frame}
          <div class="row wrap add">
            {#if added.has(p.id)}
              <span class="small ok">Added ✓</span>
            {:else}
              <span class="xsmall muted">Add as</span>
              {#each sensor.states as s (s.key)}
                <button class="btn sm state" style:border-left-color={s.color} onclick={() => addAs(p, s.key)}>{s.name}</button>
              {/each}
            {/if}
          </div>
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
    align-items: center;
    gap: var(--space-4);
    padding: var(--space-2);
    padding-right: var(--space-4);
  }
  img {
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
</style>
