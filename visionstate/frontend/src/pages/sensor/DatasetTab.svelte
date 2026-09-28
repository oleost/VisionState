<script lang="ts">
  import { api } from '../../lib/api';
  import { toastError } from '../../lib/app.svelte';
  import SampleGrid from '../../lib/components/SampleGrid.svelte';
  import type { SampleItem, Sensor } from '../../lib/types';
  import { DATASET_PAGE_SIZE } from '../../lib/ui';

  let { sensor, onchange }: { sensor: Sensor; onchange: () => void } = $props();

  // "" = everything, "unlabelled", or a state key
  let filter = $state('');
  let items = $state<SampleItem[]>([]);
  let total = $state(0);
  let limit = $state(DATASET_PAGE_SIZE);

  async function load() {
    try {
      const params =
        filter === '' ? { filter: 'all' } : filter === 'unlabelled' ? { filter: 'unlabelled' } : { filter: 'all', state: filter };
      const result = await api.samples(sensor.id, { ...params, limit });
      items = result.items;
      total = result.total;
    } catch (err) {
      toastError(err);
    }
  }

  $effect(() => {
    void filter;
    void limit;
    load();
  });

  const chips = $derived([
    { id: '', label: 'All', count: sensor.counts.labelled + sensor.counts.unlabelled, color: null },
    ...sensor.states.map((s) => {
      const c = sensor.counts.per_state[s.key] ?? { day: 0, night: 0 };
      return { id: s.key, label: s.name, count: c.day + c.night, color: s.color };
    }),
    { id: 'unlabelled', label: 'Unlabelled', count: sensor.counts.unlabelled, color: null },
  ]);
</script>

<div class="row wrap">
  {#each chips as c (c.id)}
    <button class="filter" class:active={filter === c.id} onclick={() => (filter = c.id)}>
      {#if c.color}<span class="dot" style:background={c.color}></span>{/if}
      {c.label} <span class="mono faint">{c.count}</span>
    </button>
  {/each}
</div>

<SampleGrid
  {sensor}
  {items}
  onchange={() => {
    load();
    onchange();
  }}
/>

{#if total > items.length}
  <button class="btn" style="align-self:center" onclick={() => (limit += DATASET_PAGE_SIZE)}>
    Show more ({total - items.length} left)
  </button>
{/if}

<style>
  .filter {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    height: 34px;
    padding: 0 14px;
    border-radius: var(--radius-pill);
    background: var(--c-surface);
    border: 1px solid var(--c-border);
    color: var(--c-text-2);
    font: 500 var(--fs-md) var(--font-body);
    cursor: pointer;
  }
  .filter.active {
    border-color: var(--c-accent);
    color: var(--c-text);
  }
</style>
