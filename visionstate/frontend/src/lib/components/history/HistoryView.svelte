<script lang="ts">
  // The history, filtered: the History page (any sensors) and each sensor's History tab (`locked`
  // to that sensor). The filter lives in the page URL (lib/history.ts), so it can be shared and
  // survives a reload. New rows are not added while the list is read (a tap would land elsewhere):
  // the page asks every few seconds whether any arrived and offers to show them.
  import { onDestroy, untrack, type Component } from 'svelte';
  import { api } from '../../api';
  import { app, stateInfo, toastError } from '../../app.svelte';
  import { fromQuery, keyName, queryString, toFilter, toQuery, windowLabel, type HistoryView } from '../../history';
  import { objectColor, objectKeys } from '../../objects';
  import { href, paths, route, setQuery } from '../../router.svelte';
  import type { HistoryFacets, HistoryFilter, Prediction, Sensor, SensorKind } from '../../types';
  import { HISTORY_EVENTS } from '../../ui';
  import MultiSelect, { type Option } from '../MultiSelect.svelte';
  import ObjectRow from './ObjectRow.svelte';
  import ReadingRow from './ReadingRow.svelte';
  import StateRow from './StateRow.svelte';

  let {
    sensors,
    locked = null,
    intro,
    onchange = () => {},
  }: { sensors: Sensor[]; locked?: Sensor | null; intro: string; onchange?: () => void } = $props();

  type RowProps = { item: Prediction; sensor: Sensor; showSensor?: boolean; onchange?: () => void };
  const ROWS: Record<SensorKind, Component<RowProps>> = { single_state: StateRow, objects: ObjectRow, reading: ReadingRow };

  const view = $derived(fromQuery(route.query));
  const shown = $derived<HistoryView>(locked ? { ...view, sensor: [locked.id] } : view);
  const byId = $derived(new Map(sensors.map((s) => [s.id, s])));
  const settings = $derived(app.config?.history);

  let items = $state<Prediction[] | null>(null);
  let total = $state(0);
  let next = $state<string | null>(null);
  let newest = $state<string | null>(null);
  let fresh = $state(0); // rows that arrived since the list was loaded
  let facets = $state<HistoryFacets | null>(null);
  let loadingMore = $state(false);
  let active: HistoryFilter = {}; // the filter of the list shown ("the last hours" fixed when loaded)
  let generation = 0; // a newer load makes answers to older requests stale

  async function load() {
    const mine = ++generation;
    const filter = toFilter(shown);
    try {
      const [page, found] = await Promise.all([
        api.history(filter, { order: shown.order, limit: settings?.page_size }),
        api.historyFacets(filter),
      ]);
      if (mine !== generation) return;
      active = filter;
      items = page.items;
      total = page.total;
      next = page.next;
      newest = page.newest;
      fresh = 0;
      facets = found;
    } catch (err) {
      if (mine === generation) toastError(err);
    }
  }

  // Reload when the filter changes, and only then (a locked sensor is polled: a new object each time).
  const key = $derived(JSON.stringify(shown));
  $effect(() => {
    void key;
    untrack(load);
  });

  async function more() {
    if (!next || loadingMore) return;
    const mine = generation;
    loadingMore = true;
    try {
      const page = await api.history(active, { order: shown.order, limit: settings?.page_size, cursor: next });
      if (mine !== generation) return;
      items = [...(items ?? []), ...page.items];
      next = page.next;
    } catch (err) {
      toastError(err);
    } finally {
      loadingMore = false;
    }
  }

  let checking = false;
  async function check() {
    if (checking || items === null || document.visibilityState !== 'visible') return;
    const mine = generation;
    checking = true;
    try {
      const page = await api.history(active, { limit: 0, newerThan: newest });
      if (mine !== generation) return;
      if (newest === null) {
        if (page.total) await load(); // nothing was listed: nothing to keep in place
      } else {
        fresh = page.total;
      }
    } catch {
      /* the next check tries again */
    } finally {
      checking = false;
    }
  }
  const timer = setInterval(check, (app.config?.history.poll_s ?? 10) * 1000);
  onDestroy(() => clearInterval(timer));

  function update(change: Partial<HistoryView>) {
    setQuery(toQuery({ ...view, ...change }));
  }
  // Date pickers report a value on input in some browsers (WebKit) and on change in others.
  function setTime(field: 'since' | 'until', value: string) {
    if (value !== view[field]) update({ [field]: value });
  }

  const filtered = $derived(
    shown.key.length > 0 || shown.event.length > 0 || shown.waiting || shown.window !== '' || (!locked && shown.sensor.length > 0),
  );
  const clear = () => setQuery(shown.order === 'newest' ? {} : { order: shown.order });

  // --- what each filter offers, with how many rows choosing it would find ----------------------

  const sensorOptions = $derived<Option[]>(
    sensors.map((s) => ({ value: String(s.id), label: s.name, count: facets?.sensors.find((f) => f.id === s.id)?.count ?? 0 })),
  );

  const keyOptions = $derived.by<Option[]>(() => {
    const options = new Map<string, Option>();
    for (const f of facets?.keys ?? []) {
      const sensor = byId.get(f.sensor_id);
      const option = options.get(f.key);
      if (option) option.count = (option.count ?? 0) + f.count;
      else {
        const color = sensor?.kind === 'objects' ? objectColor(objectKeys(sensor), f.key) : sensor ? stateInfo(sensor, f.key).color : undefined;
        options.set(f.key, { value: f.key, label: keyName(sensor, f.key), count: f.count, color });
      }
    }
    for (const k of shown.key) if (!options.has(k)) options.set(k, { value: k, label: keyName(undefined, k), count: 0 });
    return [...options.values()].sort((a, b) => a.label.localeCompare(b.label));
  });

  // Events of the kinds in view: the chosen sensors', else every sensor's.
  const kinds = $derived.by<SensorKind[]>(() => {
    const chosen = shown.sensor.length ? sensors.filter((s) => shown.sensor.includes(s.id)) : sensors;
    return [...new Set(chosen.map((s) => s.kind))];
  });
  const eventOptions = $derived<Option[]>(
    kinds
      .flatMap((kind) => app.config?.history_events[kind] ?? [])
      .map((e) => ({ value: e, label: HISTORY_EVENTS[e] ?? e, count: facets?.events.find((f) => f.event === e)?.count ?? 0 })),
  );
</script>

<p class="small muted">
  {intro} Kept as set under <a href={href(paths.settings())}>Settings → Storage</a>.
  {#if locked}<a href={href(paths.history(queryString({ ...view, sensor: [locked.id] })))}>Open in all history</a>{/if}
</p>

<div class="filters" role="group" aria-label="Filter the history">
  {#if !locked}
    <MultiSelect label="Sensors" options={sensorOptions} selected={shown.sensor.map(String)} onchange={(v) => update({ sensor: v.map(Number) })} />
  {/if}
  <MultiSelect label="What" options={keyOptions} selected={shown.key} onchange={(v) => update({ key: v })} />
  <MultiSelect label="Event" options={eventOptions} selected={shown.event} onchange={(v) => update({ event: v })} />
  <label class="pick">
    <span class="sr-only">Time</span>
    <select class="input sm" value={shown.window} onchange={(e) => update({ window: e.currentTarget.value })}>
      <option value="">Any time</option>
      {#each settings?.window_presets_h ?? [] as hours (hours)}<option value={String(hours)}>{windowLabel(hours)}</option>{/each}
      <option value="custom">From … to …</option>
    </select>
  </label>
  <label class="pick">
    <span class="sr-only">Order</span>
    <select class="input sm" value={shown.order} onchange={(e) => update({ order: e.currentTarget.value === 'oldest' ? 'oldest' : 'newest' })}>
      <option value="newest">Newest first</option>
      <option value="oldest">Oldest first</option>
    </select>
  </label>
  <label class="check">
    <input type="checkbox" checked={shown.waiting} onchange={(e) => update({ waiting: e.currentTarget.checked })} />
    Waiting for review
  </label>
  {#if filtered}<button type="button" class="btn sm ghost" onclick={clear}>Clear filters</button>{/if}
</div>

{#if shown.window === 'custom'}
  <div class="row wrap custom">
    <label class="field">From <input class="input sm" type="datetime-local" value={shown.since} oninput={(e) => setTime('since', e.currentTarget.value)} onchange={(e) => setTime('since', e.currentTarget.value)} /></label>
    <label class="field">To <input class="input sm" type="datetime-local" value={shown.until} oninput={(e) => setTime('until', e.currentTarget.value)} onchange={(e) => setTime('until', e.currentTarget.value)} /></label>
  </div>
{/if}

<div class="row status">
  <span class="small muted" data-testid="history-total">
    {#if items !== null}{`${total === 1 ? '1 entry' : `${total} entries`}${items.length < total ? ` · showing ${items.length}` : ''}`}{/if}
  </span>
  <span class="spacer"></span>
  {#if fresh > 0}
    <button type="button" class="btn sm accent-outline" onclick={load}>Show {fresh} new</button>
  {/if}
</div>

{#if items === null}
  <p class="muted">Loading…</p>
{:else if !items.length}
  {#if filtered}
    <div class="col empty">
      <p class="muted">Nothing matches these filters.</p>
      <button type="button" class="btn sm" onclick={clear}>Clear filters</button>
    </div>
  {:else}
    <p class="muted">Nothing yet. The history fills up as the {locked ? 'sensor runs' : 'sensors run'}.</p>
  {/if}
{:else}
  <div class="col list">
    {#each items as item (item.id)}
      {@const sensor = byId.get(item.sensor_id)}
      {#if sensor}
        {@const Row = ROWS[sensor.kind]}
        <Row {item} {sensor} showSensor={!locked} {onchange} />
      {/if}
    {/each}
  </div>
  {#if next}
    <button type="button" class="btn more" disabled={loadingMore} onclick={more}>
      {loadingMore ? 'Loading…' : `Load more (${total - items.length} left)`}
    </button>
  {/if}
{/if}

<style>
  .filters {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-2);
  }
  .pick {
    min-width: 0;
  }
  .pick select {
    width: auto;
    max-width: 100%;
    height: 36px;
  }
  .custom {
    gap: var(--space-3);
  }
  .custom .field {
    flex: 1 1 200px;
    font-weight: 500;
  }
  .status {
    min-height: 32px; /* the same height with or without the "new" button: nothing below moves */
  }
  .list {
    gap: var(--space-2);
  }
  .more {
    align-self: center;
  }
  .empty {
    align-items: flex-start;
  }
</style>
