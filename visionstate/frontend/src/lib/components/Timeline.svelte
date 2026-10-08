<script lang="ts">
  // Coloured bar of published states over the last TIMELINE_HOURS.
  import { api } from '../api';
  import { app, stateInfo } from '../app.svelte';
  import type { Prediction, Sensor } from '../types';
  import { TIMELINE_HOURS } from '../ui';

  let { sensor }: { sensor: Sensor } = $props();

  let segments = $state<{ w: number; color: string; name: string }[]>([]);

  async function load() {
    const end = Date.now();
    const start = end - TIMELINE_HOURS * 3_600_000;
    const since = new Date(start).toISOString();
    const changes = { sensor: [sensor.id], event: ['change'] };
    // The changes inside the window, and the last one before it (the state the window starts in).
    const [inside, before] = await Promise.all([
      api.history({ ...changes, since }, { order: 'oldest', limit: app.config?.history.max_page_size }),
      api.history({ ...changes, until: since }, { limit: 1 }),
    ]);
    const point = (p: Prediction) => ({ t: Date.parse(p.created_at), key: p.published_key ?? '' });
    const first = before.items[0];
    const points = [...(first ? [{ t: start, key: first.published_key ?? '' }] : []), ...inside.items.map(point)].filter((p) => p.key);
    const result = [];
    for (let i = 0; i < points.length; i++) {
      const from = Math.max(points[i].t, start);
      const to = i + 1 < points.length ? points[i + 1].t : end;
      const info = stateInfo(sensor, points[i].key);
      result.push({ w: to - from, color: info.color, name: info.name });
    }
    segments = result;
  }

  // Reload when the published state changes, otherwise at most once a minute.
  const REFRESH_MS = 60_000;
  let loadedFor: string | null | undefined;
  let loadedAt = 0;

  $effect(() => {
    const published = sensor.live.published;
    if (published === loadedFor && Date.now() - loadedAt < REFRESH_MS) return;
    loadedFor = published;
    loadedAt = Date.now();
    load().catch(() => {});
  });
</script>

<div class="timeline">
  <div class="bar" aria-label="State over the last {TIMELINE_HOURS} hours">
    {#if segments.length}
      {#each segments as seg, i (i)}
        <div style:flex-grow={seg.w} style:background={seg.color} title={seg.name}></div>
      {/each}
    {:else}
      <div class="empty"></div>
    {/if}
  </div>
  <div class="labels"><span>{TIMELINE_HOURS} h ago</span><span>now</span></div>
</div>

<style>
  .timeline {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .bar {
    display: flex;
    gap: 2px;
    height: 8px;
    border-radius: 4px;
    overflow: hidden;
  }
  .bar div {
    flex-basis: 0;
  }
  .empty {
    flex-grow: 1;
    background: var(--c-surface-3);
  }
  .labels {
    display: flex;
    justify-content: space-between;
    font-size: var(--fs-xs);
    color: var(--c-faint);
  }
</style>
