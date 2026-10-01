<script lang="ts">
  import { onDestroy } from 'svelte';
  import { api } from '../lib/api';
  import { app, shownConfidence, stateInfo, toast, toastError } from '../lib/app.svelte';
  import AnalysedFrame from '../lib/components/AnalysedFrame.svelte';
  import Icon from '../lib/components/Icon.svelte';
  import LiveFrame from '../lib/components/LiveFrame.svelte';
  import ObjectChips from '../lib/components/ObjectChips.svelte';
  import StatePill from '../lib/components/StatePill.svelte';
  import Timeline from '../lib/components/Timeline.svelte';
  import { plural, pct } from '../lib/format';
  import { isObjectSensor, objectName } from '../lib/objects';
  import { isReadingSensor, readingText, readingUnit } from '../lib/reading';
  import { READING_MODE_INFO } from '../lib/ui';
  import { go, href, paths } from '../lib/router.svelte';
  import type { Sensor } from '../lib/types';
  import { POLL, SENSOR_STATUS } from '../lib/ui';

  let sensors = $state<Sensor[] | null>(null);
  let importInput: HTMLInputElement;
  let timer: ReturnType<typeof setInterval>;

  async function load() {
    try {
      sensors = await api.sensors();
    } catch (err) {
      app.error = (err as Error).message;
    }
  }

  load();
  timer = setInterval(load, POLL.dashboard);
  onDestroy(() => clearInterval(timer));

  async function importFile(e: Event) {
    const file = (e.currentTarget as HTMLInputElement).files?.[0];
    if (!file) return;
    try {
      const { id } = await api.importBundle(file);
      toast('Sensor imported');
      go(paths.sensor(id));
    } catch (err) {
      toastError(err);
    } finally {
      importInput.value = '';
    }
  }


  function meta(s: Sensor) {
    if (isObjectSensor(s)) return `Looks for ${(s.objects?.classes ?? []).map(objectName).join(', ')}`;
    if (isReadingSensor(s) && s.reading) {
      const unit = readingUnit(s.reading);
      return `Reads a ${READING_MODE_INFO[s.reading.mode].title.toLowerCase()}${unit ? ` in ${unit}` : ''}`;
    }
    const n = s.counts.labelled;
    if (s.model?.accuracy != null) return `${n} samples · ${pct(s.model.accuracy)} accuracy`;
    const target = app.config?.quality.min_samples_per_state ?? 20;
    return `${n} samples · label about ${target} per state`;
  }
</script>

<div class="page">
  <header class="row wrap">
    <div class="col" style="gap:6px">
      <h1>Sensors</h1>
      <p class="muted">
        {plural(sensors?.length ?? 0, "sensor")}
      </p>
    </div>
    <span class="spacer"></span>
    <div class="row head-actions">
      <input bind:this={importInput} type="file" accept=".zip" class="sr-only" onchange={importFile} id="import-file" />
      <label class="btn" for="import-file"><Icon name="upload" /> Import</label>
      <a class="btn primary" href={href(paths.newSensor())}><Icon name="plus" /> New sensor</a>
    </div>
  </header>

  {#if app.status?.backbone_error}
    <div class="notice danger">
      <Icon name="alert" /><span>The AI model could not be loaded: {app.status.backbone_error}</span>
    </div>
  {/if}
  {#if app.status && !app.status.mqtt.connected}
    <div class="notice warn">
      <Icon name="alert" />
      <span
        >MQTT is not connected{app.status.mqtt.error ? `: ${app.status.mqtt.error}` : ''}. Sensors still run here, but Home
        Assistant will not receive their states. Install the Mosquitto broker app or set the MQTT options.</span
      >
    </div>
  {/if}

  {#if sensors === null}
    <p class="muted">Loading…</p>
  {:else}
    <div class="grid-3">
      {#each sensors as s (s.id)}
        {@const current = stateInfo(s, s.live.published)}
        {@const status = SENSOR_STATUS[s.status]}
        <article class="card sensor">
          <a class="thumb" href={href(paths.sensor(s.id))} aria-label="Open {s.name}">
            {#if isObjectSensor(s)}
              <AnalysedFrame sensor={s} labels={false} />
              <span class="pill-pos"><ObjectChips sensor={s} overlay /></span>
            {:else if isReadingSensor(s)}
              <LiveFrame sensorId={s.id} roi={s.roi} cached interval={POLL.thumbnail} showLive={false} />
              <span class="pill-pos"><StatePill overlay name={readingText(s)} color="var(--c-accent)" /></span>
            {:else}
              <LiveFrame sensorId={s.id} roi={s.roi} cached interval={POLL.thumbnail} showLive={false} />
              <span class="pill-pos">
                <StatePill overlay name={current.name} color={current.color} confidence={shownConfidence(s)} />
              </span>
            {/if}
          </a>
          <div class="body col">
            <div class="row">
              <h2><a href={href(paths.sensor(s.id))}>{s.name}</a></h2>
              <span class="spacer"></span>
              {#if s.training}<span class="chip info">Training…</span>{:else}<span class="chip {status.tone}">{status.label}</span>{/if}
            </div>
            <div class="col" style="gap:2px">
              <span class="mono xsmall muted">{s.entity_id}</span>
              <span class="small muted">{meta(s)}</span>
            </div>
            {#if isObjectSensor(s) || isReadingSensor(s)}
              <div class="actions">
                <a class="btn sm" href={href(paths.sensor(s.id, 'live'))}>Live</a>
                <a class="btn sm" href={href(paths.sensor(s.id, 'history'))}>History</a>
                <a class="btn sm" href={href(paths.sensor(s.id, 'settings'))}>Settings</a>
              </div>
            {:else}
              <Timeline sensor={s} />
              <div class="actions">
                <a class="btn sm" href={href(paths.sensor(s.id, 'label'))}>Label</a>
                <a class="btn sm" href={href(paths.sensor(s.id, 'upload'))}>Upload</a>
                <a class="btn sm" href={href(paths.sensor(s.id, 'quality'))}>Quality</a>
              </div>
            {/if}
          </div>
        </article>
      {/each}
      <a class="card new" href={href(paths.newSensor())}>
        <span class="plus"><Icon name="plus" size={22} /></span>
        <h2>New sensor</h2>
        <span class="small muted">Watch a state you teach it, find people, cars and animals, or read a number.</span>
      </a>
    </div>
  {/if}
</div>

<style>
  header {
    align-items: flex-end;
  }
  .head-actions {
    gap: var(--space-2);
  }
  @media (max-width: 600px) {
    /* Both buttons together on their own row under the title on phones. */
    .head-actions {
      flex-basis: 100%;
    }
  }
  .sensor {
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }
  .thumb {
    position: relative;
    display: flex;
    flex-direction: column;
    justify-content: center; /* a wide frame (a display) sits in the middle, not on top of an empty band */
    aspect-ratio: 16 / 9;
    overflow: hidden;
    background: var(--c-sunken);
  }
  .pill-pos {
    position: absolute;
    left: 12px;
    bottom: 12px;
  }
  .body {
    padding: var(--space-4) 18px 18px;
  }
  h2 a {
    color: var(--c-text);
  }
  .actions {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: var(--space-2);
  }
  .new {
    border: 2px dashed var(--c-border-strong);
    background: transparent;
    min-height: 360px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--space-2);
    text-align: center;
    padding: var(--space-6);
    color: var(--c-text);
  }
  @media (max-width: 760px) {
    .new {
      min-height: 0; /* stacked under the sensors, no need to match their height */
    }
  }
  .plus {
    width: 48px;
    height: 48px;
    border-radius: 12px;
    background: var(--c-surface-2);
    color: var(--c-accent);
    display: flex;
    align-items: center;
    justify-content: center;
  }
</style>
