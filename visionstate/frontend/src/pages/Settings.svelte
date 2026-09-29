<script lang="ts">
  import { api } from '../lib/api';
  import { app, refreshStatus, toast, toastError } from '../lib/app.svelte';
  import Icon from '../lib/components/Icon.svelte';
  import ReviewRulesEditor from '../lib/components/ReviewRulesEditor.svelte';
  import { mb } from '../lib/format';
  import { go, paths } from '../lib/router.svelte';
  import type { ReviewRules, SettingsInfo } from '../lib/types';

  let info = $state<SettingsInfo | null>(null);
  let backbone = $state('');
  let provider = $state('');
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
      provider = s.execution_provider;
    })
    .catch(toastError);

  const selected = $derived(info?.backbones.find((b) => b.id === backbone));
  const changed = $derived(info !== null && (backbone !== info.backbone || provider !== info.execution_provider));

  async function save() {
    saving = true;
    try {
      info = await api.saveSettings(backbone, provider);
      toast('AI model changed — all sensors are retraining');
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
      const { id } = await api.importBundle(file);
      toast('Sensor imported');
      go(paths.sensor(id, 'quality'));
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
            label: 'AI model',
            value: app.status.backbone_error || `${app.status.backbone_name} on ${app.status.provider}`,
            ok: !app.status.backbone_error,
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
          Backbone
          <select class="input" bind:value={backbone}>
            {#each info.backbones as b (b.id)}
              <option value={b.id}>{b.name}{b.installed ? '' : ` · download ${mb(b.size)}`}</option>
            {/each}
          </select>
          {#if selected}<span class="hint">{selected.description}</span>{/if}
        </label>
        <label class="field">
          Runs on
          <select class="input" bind:value={provider}>
            {#each info.providers as p (p)}<option value={p}>{p.replace('ExecutionProvider', '')}</option>{/each}
          </select>
          <span class="hint">Only accelerators available in this installation are listed.</span>
        </label>
        <div class="row">
          <button class="btn primary" disabled={!changed || saving} onclick={save}>
            {saving ? 'Switching (may download)…' : 'Apply'}
          </button>
          <span class="xsmall faint">Switching retrains every sensor from its stored images.</span>
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
