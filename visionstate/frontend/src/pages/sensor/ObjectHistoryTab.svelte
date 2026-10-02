<script lang="ts">
  // Object sensors: when each object appeared and cleared, with the frame and its boxes.
  import { api } from '../../lib/api';
  import { href, paths } from '../../lib/router.svelte';
  import { toastError } from '../../lib/app.svelte';
  import DetectionBoxes from '../../lib/components/DetectionBoxes.svelte';
  import Icon from '../../lib/components/Icon.svelte';
  import RoiEditor from '../../lib/components/RoiEditor.svelte';
  import { dateTime, pct } from '../../lib/format';
  import { objectColor, objectCount, objectName } from '../../lib/objects';
  import type { Prediction, Sensor } from '../../lib/types';

  let { sensor }: { sensor: Sensor } = $props();

  let items = $state<Prediction[] | null>(null);
  let open = $state<number | null>(null);

  // Reload when an object appears or clears.
  const onKey = $derived((sensor.objects?.live ?? []).map((o) => `${o.key}:${o.on}`).join());
  $effect(() => {
    void onKey;
    api
      .history(sensor.id)
      .then((r) => (items = r))
      .catch(toastError);
  });

  const classes = $derived(sensor.objects?.classes ?? []);

  function inFrame(p: Prediction): string {
    const counts = new Map<string, number>();
    for (const d of p.detections ?? []) counts.set(d.key, (counts.get(d.key) ?? 0) + 1);
    return [...counts].map(([key, n]) => objectCount(n, key)).join(', ');
  }
</script>

<p class="small muted">
  When each object appeared and cleared, kept as set under <a href={href(paths.settings())}>Settings → Storage</a>. Tap a row to see the frame with what the AI
  found.
</p>

{#if items === null}
  <p class="muted">Loading…</p>
{:else if !items.length}
  <p class="muted">Nothing yet. Every time an object appears or clears, it is listed here.</p>
{:else}
  <div class="col list">
    {#each items as p (p.id)}
      {@const on = p.published_key === 'on'}
      <div class="card item" class:open={open === p.id}>
        <button class="row head" onclick={() => (open = open === p.id ? null : p.id)} aria-expanded={open === p.id}>
          {#if p.has_frame}<img src={api.historyImageUrl(p.id, 'thumb')} alt="" loading="lazy" />{/if}
          <span class="col" style="gap:4px;flex-grow:1;min-width:0">
            <span class="row wrap" style="gap:8px">
              <strong class="row" style="gap:8px">
                <span class="dot" style:background={on ? objectColor(classes, p.state_key) : 'var(--c-unknown)'}></span>
                {objectName(p.state_key)}
                {on ? 'detected' : 'cleared'}
              </strong>
              {#if on && p.confidence}<span class="mono small muted">{pct(p.confidence)}</span>{/if}
            </span>
            <span class="xsmall faint">{dateTime(p.created_at)}</span>
            {#if p.detections?.length}<span class="xsmall muted">In the frame: {inFrame(p)}</span>{/if}
          </span>
          <Icon name={open === p.id ? 'chevron-up' : 'chevron-down'} size={16} />
        </button>
        {#if open === p.id && p.has_frame}
          <div class="full">
            <RoiEditor src={api.historyImageUrl(p.id)} roi={sensor.roi}>
              <DetectionBoxes detections={p.detections ?? []} {classes} />
            </RoiEditor>
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
