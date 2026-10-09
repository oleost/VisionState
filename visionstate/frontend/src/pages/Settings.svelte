<script lang="ts">
  import { api } from '../lib/api';
  import { app, refreshStatus, toast, toastError } from '../lib/app.svelte';
  import Icon from '../lib/components/Icon.svelte';
  import ReminderCard from '../lib/components/ReminderCard.svelte';
  import ReviewRulesEditor from '../lib/components/ReviewRulesEditor.svelte';
  import StorageCard from '../lib/components/StorageCard.svelte';
  import { mb } from '../lib/format';
  import { go, paths } from '../lib/router.svelte';
  import type { ReviewRules, SettingsInfo } from '../lib/types';

  let info = $state<SettingsInfo | null>(null);
  let backbone = $state('');
  let detector = $state('');
  let reader = $state('');
  let saving = $state(false);
  let importInput: HTMLInputElement;
  let reviewRules = $state<ReviewRules | null>(null);
  let savingReview = $state(false);

  api
    .reviewRules()
    .then((r) => (reviewRules = r))
    .catch(toastError);

  async function saveReview() {
    if (!reviewRules) return;
    savingReview = true;
    try {
      reviewRules = await api.saveReviewRules(reviewRules);
      toast('Review rules saved');
    } catch (err) {
      toastError(err);
    } finally {
      savingReview = false;
    }
  }

  api
    .settings()
    .then((s) => {
      info = s;
      backbone = s.backbone;
      detector = s.detector;
      reader = s.reader;
    })
    .catch(toastError);

  const selected = $derived(info?.backbones.find((b) => b.id === backbone));
  const selectedDetector = $derived(info?.detectors.find((d) => d.id === detector));
  const selectedReader = $derived(info?.readers.find((r) => r.id === reader));
  const wheelReader = $derived(info?.wheel_readers.find((r) => r.id === info?.wheel_reader));
  const retrains = $derived(info !== null && backbone !== info.backbone);
  const changed = $derived(info !== null && (retrains || detector !== info.detector || reader !== info.reader));

  async function save() {
    saving = true;
    try {
      const retrained = retrains;
      info = await api.saveSettings({ backbone, detector, reader });
      toast(retrained ? 'AI model changed — state sensors are retraining' : 'AI models changed');
      refreshStatus();
    } catch (err) {
      toastError(err);
    } finally {
      saving = false;
    }
  }

  async function importFile(e: Event) {
    const file = (e.currentTarget as HTMLInputElement).files?.[0];
    if (!file) return;
    try {
      const { id, skipped } = await api.importBundle(file);
      toast(skipped ? `Sensor imported; ${skipped} unreadable image${skipped === 1 ? '' : 's'} skipped` : 'Sensor imported');
      go(paths.sensor(id));
    } catch (err) {
      toastError(err);
    } finally {
      importInput.value = '';
    }
  }

  const statusRows = $derived(
    app.status
      ? [
          { label: 'Version', value: app.status.version, ok: true },
          {
            label: 'MQTT',
            value: app.status.mqtt.connected ? `connected to ${app.status.mqtt.host}` : app.status.mqtt.error || 'not connected',
            ok: app.status.mqtt.connected,
          },
          { label: 'Home Assistant API', value: app.status.home_assistant ? 'available' : 'not configured', ok: app.status.home_assistant },
          {
            label: 'Trigger events',
            value: !app.status.ha_events.entities
              ? 'no trigger entities configured'
              : !app.status.ha_events.enabled
                ? 'Home Assistant API not available'
                : app.status.ha_events.connected
                ? `listening to ${app.status.ha_events.entities} entities`
                : app.status.ha_events.error || 'connecting…',
            ok: !app.status.ha_events.entities || app.status.ha_events.connected,
          },
          {
            label: 'State model',
            value: app.status.backbone_error || app.status.backbone_name || '—',
            ok: !app.status.backbone_error,
          },
          {
            label: 'Object detector',
            value: app.status.detector_error || app.status.detector_name || 'loaded when an object sensor exists',
            ok: !app.status.detector_error,
          },
          {
            label: 'Number reader',
            value: app.status.reader_error || app.status.reader_name || 'loaded when a display, or a counter read as text, is read',
            ok: !app.status.reader_error,
          },
          {
            label: 'Wheel reader',
            value: app.status.wheel_reader_error || app.status.wheel_reader_name || 'loaded when a mechanical counter is read',
            ok: !app.status.wheel_reader_error,
          },
        ]
      : [],
  );
</script>

<div class="page">
  <h1>Settings</h1>
  <div class="grid-2">
    <section class="card pad col">
      <h3>Status</h3>
      {#each statusRows as row (row.label)}
        <div class="row small">
          <span class="dot" style:background={row.ok ? 'var(--c-accent)' : 'var(--c-danger)'}></span>
          <span class="muted" style="width:150px">{row.label}</span>
          <span>{row.value}</span>
        </div>
      {/each}
    </section>

    <section class="card pad col">
      <h3>AI model</h3>
      {#if info}
        <label class="field">
          State sensors (learned)
          <select class="input" bind:value={backbone}>
            {#each info.backbones as b (b.id)}
              <option value={b.id}>{b.name}{b.installed ? '' : ` · download ${mb(b.size)}`}</option>
            {/each}
          </select>
          {#if selected}<span class="hint">{selected.description}</span>{/if}
        </label>
        <label class="field">
          Object sensors
          <select class="input" bind:value={detector}>
            {#each info.detectors as d (d.id)}
              <option value={d.id}>{d.name}{d.installed ? '' : ` · download ${mb(d.size)}`}</option>
            {/each}
          </select>
          {#if selectedDetector}
            <span class="hint"
              >{selectedDetector.description} Pretrained on COCO ({selectedDetector.license}),
              <a href={selectedDetector.source} target="_blank" rel="noreferrer">source</a>.</span
            >
          {/if}
        </label>
        <label class="field">
          Reading sensors: displays
          <select class="input" bind:value={reader}>
            {#each info.readers as r (r.id)}
              <option value={r.id}>{r.name}{r.installed ? '' : ` · download ${mb(r.size)}`}</option>
            {/each}
          </select>
          {#if selectedReader}
            <span class="hint"
              >{selectedReader.description} PaddleOCR ({selectedReader.license}),
              <a href={selectedReader.source} target="_blank" rel="noreferrer">source</a>. Also reads mechanical counters set to
              <em>Read with → Text reader</em>.</span
            >
          {/if}
        </label>
        {#if wheelReader}
          <div class="field" data-testid="wheel-reader">
            Reading sensors: mechanical counters
            <span class="model">{wheelReader.name} · included</span>
            <span class="hint"
              >{wheelReader.description} {wheelReader.license},
              <a href={wheelReader.source} target="_blank" rel="noreferrer">source</a>. Used by counters set to
              <em>Read with → Wheel reader</em> (the default).</span
            >
          </div>
        {/if}
        <div class="row">
          <button class="btn primary" disabled={!changed || saving} onclick={save}>
            {saving ? 'Switching (may download)…' : 'Apply'}
          </button>
          <span class="xsmall faint">A new state model retrains every state sensor from its stored images. Object and reading sensors need no training.</span>
        </div>
      {/if}
    </section>

    <section class="card pad col">
      <h3>Review queue</h3>
      <p class="small muted">Which frames are collected for review. Each sensor can override these on its Settings tab.</p>
      {#if reviewRules}
        <ReviewRulesEditor bind:value={reviewRules} />
        <button class="btn primary" style="align-self:flex-start" disabled={savingReview} onclick={saveReview}>
          {savingReview ? 'Saving…' : 'Save review rules'}
        </button>
      {/if}
    </section>

    <ReminderCard />

    <StorageCard />

    <section class="card pad col">
      <h3>Import a sensor</h3>
      <p class="small muted">Import a .zip exported from VisionState (Sensor → Export). It is added as a new sensor.</p>
      <input bind:this={importInput} type="file" accept=".zip" class="sr-only" id="settings-import" onchange={importFile} />
      <label class="btn" for="settings-import" style="align-self:flex-start"><Icon name="upload" /> Choose file</label>
    </section>

    {#if info}
      <section class="card pad col">
        <h3>App options</h3>
        <p class="small muted">Set these on the app's Configuration tab in Home Assistant.</p>
        {#each Object.entries(info.options) as [key, value] (key)}
          <div class="row small"><span class="mono muted" style="width:220px">{key}</span><span>{value}</span></div>
        {/each}
      </section>
    {/if}
  </div>
</div>

<style>
  .model {
    font: 400 var(--fs-lg) var(--font-body);
    color: var(--c-text);
  }
</style>
