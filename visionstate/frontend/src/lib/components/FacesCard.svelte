<script lang="ts">
  // Recognising people by their face, on an object sensor's Settings tab. Off by default: turning
  // it on opens a sheet that says what is stored and what is downloaded, before anything is. Its
  // changes are saved at once (not with "Save changes"), like teaching is.
  import { api } from '../api';
  import { app, toast, toastError } from '../app.svelte';
  import { size } from '../format';
  import { href, paths } from '../router.svelte';
  import type { FaceKey, FaceLimitsInfo, FaceModelInfo, FaceOverrides, FaceSettings, Sensor } from '../types';
  import { FACE_FIELDS } from '../ui';
  import FaceConsent from './FaceConsent.svelte';
  import FaceLimitsEditor from './FaceLimitsEditor.svelte';
  import FaceStats from './FaceStats.svelte';
  import Icon from './Icon.svelte';

  let { sensor, onchange }: { sensor: Sensor; onchange: () => void } = $props();

  const settings = $derived<FaceSettings>({ ...app.config!.face_defaults, ...(sensor.objects?.faces ?? {}) });
  const looksForPeople = $derived(!!sensor.objects?.classes.includes('person'));
  const status = $derived(app.status?.faces ?? null);
  const people = $derived(app.status?.faces.people ?? 0);

  let model = $state<FaceModelInfo | null>(null);
  let asking = $state(false);
  let busy = $state(false);
  let globalFaces = $state<FaceLimitsInfo | null>(null);
  // The sensor's own face limits (null: the global value), saved with their own button.
  const own = (): FaceOverrides =>
    Object.fromEntries(FACE_FIELDS.map((f) => [f.key, sensor.objects?.faces?.[f.key as FaceKey] ?? null]));
  let limitValues = $state<FaceOverrides>(own());
  const limitsChanged = $derived(JSON.stringify(limitValues) !== JSON.stringify(own()));
  api
    .settings()
    .then((s) => (model = s.face_models.find((m) => m.id === s.face_model) ?? null))
    .catch(toastError);
  api
    .faceLimits()
    .then((f) => (globalFaces = f))
    .catch(toastError);

  async function save(faces: Partial<FaceSettings>, message?: string) {
    busy = true;
    try {
      await api.updateSensor(sensor.id, { objects: { faces: { ...settings, ...faces } } });
      if (message) toast(message);
      onchange();
    } catch (err) {
      toastError(err);
    } finally {
      busy = false;
    }
  }

  async function retry() {
    try {
      await api.retryFaces();
    } catch (err) {
      toastError(err);
    }
  }
</script>

<section class="card pad col faces" data-testid="faces-card">
  <h3>Faces</h3>
  {#if !looksForPeople}
    <p class="small muted">Add Person to the objects to recognise people by their face.</p>
  {:else if !settings.enabled}
    <strong class="small">Recognise people by their face</strong>
    <p class="small muted">
      Name the people you know; each gets an on/off sensor in Home Assistant.
      {#if people}It knows the {people} {people === 1 ? 'person' : 'people'} you named on other cameras right away.{/if}
    </p>
    <div class="row">
      <button class="btn" onclick={() => (asking = true)}><Icon name="plus" size={14} /> Turn on…</button>
    </div>
  {:else}
    <label class="check">
      <input type="checkbox" checked disabled={busy} onchange={() => save({ enabled: false }, 'Face recognition is off')} />
      <span class="col" style="gap:2px">
        <strong class="small">Recognise faces</strong>
        <span class="xsmall faint">
          Off removes the people's entities from this sensor; the people stay (they are shared).
        </span>
      </span>
    </label>
    <label class="check">
      <input
        type="checkbox"
        checked={settings.ask_unknown}
        disabled={busy}
        onchange={(e) => save({ ask_unknown: e.currentTarget.checked })}
      />
      <span class="col" style="gap:2px">
        <strong class="small">Ask me about faces I do not know</strong>
        <span class="xsmall faint">
          Also asks “Who is this?” in the review queue about faces nobody is named for. A question waiting for an
          answer keeps its frame twice as long as the history.
        </span>
      </span>
    </label>
    {#if globalFaces}
      <div class="col limits" data-testid="sensor-face-limits">
        <strong class="small">Limits for this sensor</strong>
        <FaceLimitsEditor bind:value={limitValues} inherited={globalFaces.effective} inheritedFrom="the global value" />
        <button
          class="btn sm primary"
          style="align-self:flex-start"
          disabled={!limitsChanged || busy}
          onclick={() => save(limitValues, 'Face limits saved')}
        >
          Save limits
        </button>
      </div>
    {/if}

    {#if status?.error}
      <div class="notice warn col" style="gap:6px">
        <span class="small">The face model could not be loaded: {status.error}</span>
        <div class="row"><button class="btn sm" onclick={retry}><Icon name="refresh" size={14} /> Try again</button></div>
      </div>
    {:else if status?.progress}
      <div class="col" style="gap:6px">
        <span class="small muted">
          Downloading the face model… {size(status.progress.done)} of {size(status.progress.total)}
        </span>
        <progress max={status.progress.total} value={status.progress.done}></progress>
      </div>
    {:else if status && !status.loaded}
      <span class="small muted">Loading the face model…</span>
    {:else if sensor.objects}
      <FaceStats objects={sensor.objects} />
    {/if}

    <span class="xsmall faint">
      {people} {people === 1 ? 'person' : 'people'}, shared by every camera that recognises faces:
      <a href={href(paths.people())}>People</a>.
    </span>
    {#if model}
      <span class="xsmall faint">
        {model.name} · {size(model.size)} · change under <a href={href(paths.settings())}>Settings → AI model</a>.
      </span>
    {/if}
  {/if}
</section>

{#if asking}
  <FaceConsent
    name={sensor.name}
    {model}
    onclose={() => (asking = false)}
    onconfirm={async () => {
      asking = false;
      await save({ enabled: true }, 'Face recognition is on');
    }}
  />
{/if}

<style>
  .faces {
    gap: var(--space-3);
  }
  .check {
    align-items: flex-start;
  }
  .check input {
    margin-top: 3px;
  }
  .limits {
    gap: var(--space-3);
  }
  progress {
    width: 100%;
    accent-color: var(--c-accent);
  }
</style>
