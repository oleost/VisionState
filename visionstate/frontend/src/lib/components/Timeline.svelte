<script lang="ts">
  // Coloured bar of published states over the last TIMELINE_HOURS.
  import { api } from '../api';
  import { stateInfo } from '../app.svelte';
  import type { Sensor } from '../types';
  import { TIMELINE_HOURS } from '../ui';

  let { sensor }: { sensor: Sensor } = $props();

  let segments = $state<{ w: number; color: string; name: string }[]>([]);

  async function load() {
    const end = Date.now();
    const start = end - TIMELINE_HOURS * 3_600_000;
    const changes = (await api.history(sensor.id, 300))
      .filter((p) => p.is_change && p.published_key)
      .map((p) => ({ t: Date.parse(p.created_at), key: p.published_key as string }))
      .sort((a, b) => a.t - b.t);
    const before = changes.filter((c) => c.t < start).at(-1);
    const inside = changes.filter((c) => c.t >= start);
    const points = [...(before ? [{ t: start, key: before.key }] : []), ...inside];
    const result = [];
    for (let i = 0; i < points.length; i++) {
      const from = Math.max(points[i].t, start);
      const to = i + 1 < points.length ? points[i + 1].t : end;
      const info = stateInfo(sensor, points[i].key);
      result.push({ w: to - from, color: info.color, name: info.name });
    }
    segments = result;
  }

  $effect(() => {
    void sensor.live.published;
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
