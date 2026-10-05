<script lang="ts">
  // Reading sensors: every new value and the rejected readings, with the frame.
  import { api } from '../../lib/api';
  import { href, paths } from '../../lib/router.svelte';
  import { toastError } from '../../lib/app.svelte';
  import Icon from '../../lib/components/Icon.svelte';
  import RoiEditor from '../../lib/components/RoiEditor.svelte';
  import { dateTime, pct } from '../../lib/format';
  import { REJECT_REASONS, readingDetail as detail, readingUnit } from '../../lib/reading';
  import type { Prediction, Sensor } from '../../lib/types';

  let { sensor }: { sensor: Sensor } = $props();

  let items = $state<Prediction[] | null>(null);
  let open = $state<number | null>(null);

  // Reload when a new value is published or a reading is rejected.
  const refreshKey = $derived(`${sensor.reading?.value}|${sensor.reading?.last?.reason}`);
  $effect(() => {
    void refreshKey;
    api
      .history(sensor.id)
      .then((r) => (items = r))
      .catch(toastError);
  });

  const unit = $derived(sensor.reading ? readingUnit(sensor.reading) : '');
</script>

<p class="small muted">
  Every new value and every rejected reading, kept as set under <a href={href(paths.settings())}>Settings → Storage</a> (readings you verified are
  kept for good). Tap a row to see the frame; the Quality tab sums them up.
</p>

{#if items === null}
  <p class="muted">Loading…</p>
{:else if !items.length}
  <p class="muted">Nothing yet. Every new value is listed here.</p>
{:else}
  <div class="col list">
    {#each items as p (p.id)}
      {@const d = detail(p)}
      <div class="card item">
        <button class="row head" onclick={() => (open = open === p.id ? null : p.id)} aria-expanded={open === p.id}>
          {#if p.has_frame}<img src={api.historyImageUrl(p.id, 'thumb')} alt="" loading="lazy" />{/if}
          <span class="col" style="gap:4px;flex-grow:1;min-width:0">
            <span class="row wrap" style="gap:8px">
              {#if p.published_key}
                <strong class="mono">{p.published_key}{unit ? ` ${unit}` : ''}</strong>
              {:else}
                <strong>Rejected</strong>
                <span class="chip warn">{REJECT_REASONS[d.reason ?? ''] ?? d.reason}</span>
              {/if}
              <span class="mono small muted">{pct(p.confidence)}</span>
              {#if p.read_ok === false}<span class="chip danger">Misread{p.correct_value ? ` — was ${p.correct_value}` : ''}</span>
              {:else if p.read_ok}<span class="chip ok">Read correctly</span>{/if}
            </span>
            <span class="xsmall faint">{dateTime(p.created_at)}</span>
            <span class="xsmall muted">Read “{d.text || '—'}”</span>
          </span>
          <Icon name={open === p.id ? 'chevron-up' : 'chevron-down'} size={16} />
        </button>
        {#if open === p.id && p.has_frame}
          <div class="full"><RoiEditor src={api.historyImageUrl(p.id)} roi={sensor.roi} /></div>
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
    padding: var(--space-2);
  }
  .head {
    width: 100%;
    gap: var(--space-4);
    padding: 0 var(--space-2) 0 0;
    background: transparent;
    border: none;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  img {
    width: 128px;
    aspect-ratio: 16 / 10;
    object-fit: cover;
    border-radius: var(--radius-sm);
    flex-shrink: 0;
  }
  .full {
    margin-top: var(--space-2);
    border-radius: var(--radius-md);
    overflow: hidden;
  }
  @media (max-width: 600px) {
    .head {
      gap: var(--space-3);
    }
    img {
      width: 96px;
    }
  }
</style>
