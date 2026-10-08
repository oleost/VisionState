<script lang="ts">
  // A history row of an object sensor: an object appeared, cleared, was filtered away by what the
  // sensor was taught, or the sensor asked about it. It opens to the frame with its boxes, which can
  // be corrected there.
  import { api } from '../../api';
  import { dateTime, pct } from '../../format';
  import { objectColor, objectCount, objectKeys, objectName } from '../../objects';
  import type { Prediction, Sensor } from '../../types';
  import Icon from '../Icon.svelte';
  import TeachableFrame from '../TeachableFrame.svelte';

  let {
    item: p,
    sensor,
    showSensor = false,
    onchange = () => {},
  }: { item: Prediction; sensor: Sensor; showSensor?: boolean; onchange?: () => void } = $props();

  let open = $state(false);

  const classes = $derived(objectKeys(sensor));
  const own = $derived(sensor.objects?.custom ?? []);
  const on = $derived(p.published_key === 'on');
  const filtered = $derived(p.published_key === 'filtered');

  const inFrame = $derived.by(() => {
    const counts = new Map<string, number>();
    for (const d of p.detections ?? []) if (!d.filtered) counts.set(d.label ?? d.key, (counts.get(d.label ?? d.key) ?? 0) + 1);
    return [...counts].map(([key, n]) => objectCount(n, key, own)).join(', ');
  });

  const WHAT: Record<string, string> = { on: 'detected', off: 'cleared', filtered: 'filtered away', ask: '? (asked in Review)' };
</script>

<div class="card item">
  <button class="row head" onclick={() => (open = !open)} aria-expanded={open}>
    {#if p.has_frame}<img src={api.historyImageUrl(p.id, 'thumb')} alt="" loading="lazy" />{/if}
    <span class="col" style="gap:4px;flex-grow:1;min-width:0">
      {#if showSensor}<span class="xsmall muted sensor">{sensor.name}</span>{/if}
      <span class="row wrap" style="gap:8px">
        <strong class="row" style="gap:8px" class:muted={filtered}>
          <span class="dot" class:ring={filtered} style:background={on ? objectColor(classes, p.state_key) : 'var(--c-unknown)'}></span>
          {objectName(p.state_key, own)}{p.published_key === 'ask' ? '' : ' '}{WHAT[p.published_key ?? ''] ?? ''}
        </strong>
        {#if (on || filtered) && p.confidence}<span class="mono small muted">{pct(p.confidence)}</span>{/if}
      </span>
      <span class="xsmall faint">{dateTime(p.created_at)}</span>
      {#if inFrame}<span class="xsmall muted">In the frame: {inFrame}</span>{/if}
    </span>
    <Icon name={open ? 'chevron-up' : 'chevron-down'} size={16} />
  </button>
  {#if open && p.has_frame}
    <div class="full">
      <TeachableFrame {sensor} src={api.historyImageUrl(p.id)} detections={p.detections ?? []} source={{ history_id: p.id }} {onchange} />
    </div>
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
  .full :global(.teachbar) {
    border-bottom: none;
  }
  .dot.ring {
    background: transparent !important;
    border: 1.5px dashed var(--c-unknown);
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
