<script lang="ts">
  import { api } from '../lib/api';
  import { shownConfidence, stateInfo, toast, toastError } from '../lib/app.svelte';
  import CameraChanged from '../lib/components/CameraChanged.svelte';
  import Icon from '../lib/components/Icon.svelte';
  import HistoryView from '../lib/components/history/HistoryView.svelte';
  import LiveChips from '../lib/components/LiveChips.svelte';
  import StatePill from '../lib/components/StatePill.svelte';
  import TabBar from '../lib/components/TabBar.svelte';
  import { facesBusy, isObjectSensor, objectChips } from '../lib/objects';
  import { pollWhileVisible } from '../lib/poll';
  import { isReadingSensor, readingText } from '../lib/reading';
  import { href, paths } from '../lib/router.svelte';
  import type { Sensor } from '../lib/types';
  import {
    HISTORY_INTRO,
    NOT_IN_HA,
    NOT_SENT,
    POLL,
    SENSOR_STATUS,
    SENSOR_TABS,
    TABS_BY_KIND,
    type SensorTab,
    type SettingsSection,
  } from '../lib/ui';
  import DatasetTab from './sensor/DatasetTab.svelte';
  import LabelTab from './sensor/LabelTab.svelte';
  import LiveTab from './sensor/LiveTab.svelte';
  import ObjectQualityTab from './sensor/ObjectQualityTab.svelte';
  import ReadingLiveTab from './sensor/ReadingLiveTab.svelte';
  import ReadingQualityTab from './sensor/ReadingQualityTab.svelte';
  import SoundLiveTab from './sensor/SoundLiveTab.svelte';
  import SoundOwnTab from './sensor/SoundOwnTab.svelte';
  import { isSoundSensor, soundChips } from '../lib/sound';
  import QualityTab from './sensor/QualityTab.svelte';
  import SettingsTab from './sensor/SettingsTab.svelte';
  import UploadTab from './sensor/UploadTab.svelte';

  let { id, tab: requested, section = '' }: { id: number; tab: SensorTab | ''; section?: SettingsSection | '' } = $props();
  // Undo on Settings draws the form again from what is saved.
  let settingsVersion = $state(0);

  let sensor = $state<Sensor | null>(null);
  let error = $state('');

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
    return pollWhileVisible(POLL.sensor, load);
  });

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
  // Tabs depend on the kind; an unknown or missing tab opens the kind's first tab. Settings is the
  // button beside Pause. An object or sound sensor's Quality of earlier versions is its Dataset now.
  const tabs = $derived(sensor ? TABS_BY_KIND[sensor.kind].map((id) => SENSOR_TABS.find((t) => t.id === id)!) : []);
  const tab = $derived.by((): SensorTab | undefined => {
    if (requested === 'settings' || tabs.some((t) => t.id === requested)) return requested as SensorTab;
    if (requested === 'quality' && tabs.some((t) => t.id === 'dataset')) return 'dataset';
    return tabs[0]?.id;
  });
</script>

{#if error && !sensor}
  <div class="page"><div class="notice danger">{error}</div></div>
{:else if sensor && current}
  <header>
    <div class="inner">
      <div class="top">
        <div class="col" style="gap:6px">
          <div class="small muted"><a class="muted" href={href(paths.dashboard())}>Sensors</a> / {sensor.name}</div>
          <div class="row wrap namerow">
            <h1>{sensor.name}</h1>
            {#if isObjectSensor(sensor)}
              <LiveChips {...objectChips(sensor)} />
            {:else if isSoundSensor(sensor)}
              <LiveChips {...soundChips(sensor)} />
            {:else if isReadingSensor(sensor)}
              <StatePill name={readingText(sensor)} color="var(--c-accent)" />
            {:else}
              <StatePill name={current.name} color={current.color} confidence={shownConfidence(sensor)} />
            {/if}
            {#if sensor.training}<span class="chip info">Training…</span>{:else if facesBusy(sensor)}<span class="chip info"
                >{facesBusy(sensor)}</span
              >{:else if sensor.status !== 'ok'}<span
                class="chip {SENSOR_STATUS[sensor.status].tone}"
                >{isSoundSensor(sensor) && sensor.status === 'unavailable' ? 'No sound' : SENSOR_STATUS[sensor.status].label}</span
              >{/if}
            <span class="mono xsmall muted"
              >{isObjectSensor(sensor) || isSoundSensor(sensor) ? `${sensor.entity_ids.length} entities` : sensor.entity_id}</span
            >
            {#if !sensor.in_ha}<span class="chip warn" title={NOT_IN_HA.help}>{NOT_IN_HA.label}</span>{:else if !sensor.publish}<span class="chip warn" title={NOT_SENT.help}>{NOT_SENT.label}</span>{/if}
            <span class="row actions">
              <button class="btn sm" onclick={togglePause}>
                <Icon name={sensor.enabled ? 'pause' : 'play'} size={14} />
                {sensor.enabled ? 'Pause' : 'Resume'}
              </button>
              <a
                class="btn sm settings-btn"
                class:on={tab === 'settings'}
                href={href(paths.sensorSettings(sensor.id))}
                aria-current={tab === 'settings' ? 'page' : undefined}
              >
                <Icon name="settings" size={14} /> Settings
              </a>
            </span>
          </div>
        </div>
      </div>
      <TabBar
        label="Sensor sections"
        active={tab}
        tabs={tabs.map((t) => ({
          id: t.id,
          label: t.label,
          href: href(paths.sensor(sensor!.id, t.id)),
          count: t.id === 'dataset' && sensor!.kind === 'single_state' ? sensor!.counts.unlabelled : 0,
        }))}
      />
    </div>
  </header>

  <div class="page">
    {#if sensor.reading?.camera_changed}
      <div class="changed"><CameraChanged {sensor} onSettings={tab === 'settings' && section === 'detect'} onchange={load} /></div>
    {/if}
    {#if tab === 'live' && isReadingSensor(sensor)}
      <ReadingLiveTab {sensor} onchange={load} />
    {:else if tab === 'live' && isSoundSensor(sensor)}
      <SoundLiveTab {sensor} onchange={load} />
    {:else if tab === 'dataset' && isSoundSensor(sensor)}
      <SoundOwnTab {sensor} onchange={load} />
    {:else if tab === 'dataset' && isObjectSensor(sensor)}
      <ObjectQualityTab {sensor} onchange={load} />
    {:else if tab === 'live'}
      <LiveTab {sensor} onchange={load} />
    {:else if tab === 'label'}
      <LabelTab {sensor} onchange={load} />
    {:else if tab === 'dataset'}
      <!-- A state sensor's images: adding them (upload), those waiting for a label, then all of them. -->
      <UploadTab {sensor} onchange={load} />
      <h2>All images</h2>
      <DatasetTab {sensor} onchange={load} />
    {:else if tab === 'quality' && isReadingSensor(sensor)}
      <ReadingQualityTab {sensor} />
    {:else if tab === 'quality'}
      <QualityTab {sensor} />
    {:else if tab === 'history'}
      <HistoryView sensors={[sensor]} locked={sensor} intro={HISTORY_INTRO[sensor.kind]} onchange={load} />
    {:else if tab === 'settings'}
      {#key settingsVersion}
        <SettingsTab {sensor} {section} onchange={load} onreset={() => settingsVersion++} />
      {/key}
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
  .top > .col {
    min-width: 0;
  }
  h1 {
    font-size: 28px;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .actions {
    gap: var(--space-2);
    margin-left: auto;
  }
  .settings-btn.on {
    border-color: var(--c-accent);
    background: var(--c-surface-3);
    color: var(--c-text);
  }
  .changed {
    margin-bottom: var(--space-5);
  }
</style>
