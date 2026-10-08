<script lang="ts">
  // A history row of a reading sensor: a new value or a rejected reading, opening to its frame.
  import { api } from '../../api';
  import { dateTime, pct } from '../../format';
  import { REJECT_REASONS, readingDetail, readingUnit } from '../../reading';
  import type { Prediction, Sensor } from '../../types';
  import Icon from '../Icon.svelte';
  import RoiEditor from '../RoiEditor.svelte';

  let { item: p, sensor, showSensor = false }: { item: Prediction; sensor: Sensor; showSensor?: boolean } = $props();

  let open = $state(false);

  const d = $derived(readingDetail(p));
  const unit = $derived(sensor.reading ? readingUnit(sensor.reading) : '');
</script>

<div class="card item">
  <button class="row head" onclick={() => (open = !open)} aria-expanded={open}>
    {#if p.has_frame}<img src={api.historyImageUrl(p.id, 'thumb')} alt="" loading="lazy" />{/if}
    <span class="col" style="gap:4px;flex-grow:1;min-width:0">
      {#if showSensor}<span class="xsmall muted sensor">{sensor.name}</span>{/if}
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
    <Icon name={open ? 'chevron-up' : 'chevron-down'} size={16} />
  </button>
  {#if open && p.has_frame}
    <div class="full"><RoiEditor src={api.historyImageUrl(p.id)} roi={sensor.roi} /></div>
  {/if}
</div>

<style>
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
  .sensor {
    overflow-wrap: anywhere;
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
