<script lang="ts">
  import { onDestroy } from 'svelte';
  import { api } from '../lib/api';
  import { shownConfidence, stateInfo, toast, toastError } from '../lib/app.svelte';
  import Icon from '../lib/components/Icon.svelte';
  import ObjectChips from '../lib/components/ObjectChips.svelte';
  import StatePill from '../lib/components/StatePill.svelte';
  import { isObjectSensor } from '../lib/objects';
  import { href, paths } from '../lib/router.svelte';
  import type { Sensor } from '../lib/types';
  import { POLL, SENSOR_STATUS, SENSOR_TABS, TABS_BY_KIND, type SensorTab } from '../lib/ui';
  import DatasetTab from './sensor/DatasetTab.svelte';
  import HistoryTab from './sensor/HistoryTab.svelte';
  import LabelTab from './sensor/LabelTab.svelte';
  import LiveTab from './sensor/LiveTab.svelte';
  import ObjectHistoryTab from './sensor/ObjectHistoryTab.svelte';
  import QualityTab from './sensor/QualityTab.svelte';
  import SettingsTab from './sensor/SettingsTab.svelte';
  import UploadTab from './sensor/UploadTab.svelte';

  let { id, tab: requested }: { id: number; tab: SensorTab | '' } = $props();

  let sensor = $state<Sensor | null>(null);
  let error = $state('');
  let timer: ReturnType<typeof setInterval>;

  async function load() {
    try {
      sensor = await api.sensor(id);
      error = '';
    } catch (err) {
      error = (err as Error).message;
    }
  }

  $effect(() => {
    void id;
    sensor = null;
    load();
    clearInterval(timer);
    timer = setInterval(load, POLL.sensor);
  });
  onDestroy(() => clearInterval(timer));

  async function togglePause() {
    if (!sensor) return;
    try {
      sensor = await api.updateSensor(sensor.id, { enabled: !sensor.enabled });
      toast(sensor.enabled ? 'Sensor resumed' : 'Sensor paused');
    } catch (err) {
      toastError(err);
    }
  }

  const current = $derived(sensor ? stateInfo(sensor, sensor.live.published) : null);
  // Tabs depend on the kind; an unknown or missing tab opens the kind's first tab.
  const tabs = $derived(sensor ? SENSOR_TABS.filter((t) => TABS_BY_KIND[sensor!.kind].includes(t.id)) : []);
  const tab = $derived(tabs.some((t) => t.id === requested) ? (requested as SensorTab) : tabs[0]?.id);
</script>

{#if error && !sensor}
  <div class="page"><div class="notice danger">{error}</div></div>
{:else if sensor && current}
  <header>
    <div class="inner">
      <div class="row wrap top">
        <div class="col" style="gap:6px">
          <div class="small muted"><a class="muted" href={href(paths.dashboard())}>Sensors</a> / {sensor.name}</div>
          <div class="row wrap">
            <h1>{sensor.name}</h1>
            {#if isObjectSensor(sensor)}
              <ObjectChips {sensor} />
            {:else}
              <StatePill name={current.name} color={current.color} confidence={shownConfidence(sensor)} />
            {/if}
            {#if sensor.training}<span class="chip info">Training…</span>{:else if sensor.status !== 'ok'}<span
                class="chip {SENSOR_STATUS[sensor.status].tone}">{SENSOR_STATUS[sensor.status].label}</span
              >{/if}
            <span class="mono xsmall muted">{isObjectSensor(sensor) ? `${sensor.entity_ids.length} entities` : sensor.entity_id}</span>
          </div>
        </div>
        <span class="spacer"></span>
        <a class="btn sm" href={api.exportUrl(sensor.id)} download><Icon name="download" size={14} /> Export</a>
        <button class="btn sm" onclick={togglePause}>
          <Icon name={sensor.enabled ? 'pause' : 'play'} size={14} />
          {sensor.enabled ? 'Pause' : 'Resume'}
        </button>
      </div>
      <nav aria-label="Sensor sections">
        {#each tabs as t (t.id)}
          <a href={href(paths.sensor(sensor.id, t.id))} class:active={t.id === tab} aria-current={t.id === tab ? 'page' : undefined}>
            {t.label}
            {#if t.id === 'upload' && sensor.counts.unlabelled}<span class="count">{sensor.counts.unlabelled}</span>{/if}
          </a>
        {/each}
      </nav>
    </div>
  </header>

  <div class="page">
    {#if tab === 'live'}
      <LiveTab {sensor} onchange={load} />
    {:else if tab === 'label'}
      <LabelTab {sensor} onchange={load} />
    {:else if tab === 'upload'}
      <UploadTab {sensor} onchange={load} />
    {:else if tab === 'dataset'}
      <DatasetTab {sensor} onchange={load} />
    {:else if tab === 'quality'}
      <QualityTab {sensor} />
    {:else if tab === 'history' && isObjectSensor(sensor)}
      <ObjectHistoryTab {sensor} />
    {:else if tab === 'history'}
      <HistoryTab {sensor} />
    {:else if tab === 'settings'}
      <SettingsTab {sensor} onchange={load} />
    {/if}
  </div>
{:else}
  <div class="page"><p class="muted">Loading…</p></div>
{/if}

<style>
  header {
    border-bottom: 1px solid var(--c-border);
  }
  .inner {
    max-width: var(--page-max);
    margin: 0 auto;
    padding: var(--space-6) var(--page-pad) 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
  }
  .top {
    align-items: flex-start;
  }
  h1 {
    font-size: 28px;
  }
  nav {
    display: flex;
    gap: var(--space-1);
    overflow-x: auto;
  }
  nav a {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 10px 14px;
    font-weight: 500;
    color: var(--c-muted);
    border-bottom: 2px solid transparent;
    white-space: nowrap;
  }
  nav a.active {
    color: var(--c-text);
    border-bottom-color: var(--c-accent);
  }
  nav a:hover {
    color: var(--c-text);
  }
  .count {
    min-width: 18px;
    height: 18px;
    padding: 0 5px;
    border-radius: var(--radius-pill);
    background: var(--c-surface-3);
    font-size: var(--fs-xs);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: var(--c-text-2);
  }
</style>
