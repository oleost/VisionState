<script lang="ts">
  // The new sensor wizard: camera, region, what to detect (one component per kind in wizard/),
  // and when to check. Nothing is stored until "Create sensor".
  import { untrack } from 'svelte';
  import { api } from '../lib/api';
  import { app, toast, toastError } from '../lib/app.svelte';
  import Icon from '../lib/components/Icon.svelte';
  import LightEditor from '../lib/components/LightEditor.svelte';
  import LightHold from '../lib/components/LightHold.svelte';
  import PublishSwitch from '../lib/components/PublishSwitch.svelte';
  import RoiEditor from '../lib/components/RoiEditor.svelte';
  import SourcePicker from '../lib/components/SourcePicker.svelte';
  import TriggersEditor from '../lib/components/TriggersEditor.svelte';
  import { haSlug, slugify } from '../lib/format';
  import { isPolygon, toRectangle } from '../lib/roi';
  import { go, href, paths } from '../lib/router.svelte';
  import type { ReadingSettings, Roi, SensorKind, Triggers } from '../lib/types';
  import { SENSOR_KIND_INFO } from '../lib/ui';
  import ObjectsStep from './wizard/ObjectsStep.svelte';
  import ReadingStep from './wizard/ReadingStep.svelte';
  import StatesStep from './wizard/StatesStep.svelte';

  const STEPS = [
    { title: 'Camera', sub: 'Name and image source' },
    { title: 'Region', sub: 'What to look at' },
    { title: 'Detect', sub: 'States, objects or a number' },
    { title: 'Checks', sub: 'When to look (optional)' },
  ];

  let step = $state(0);
  let name = $state('');
  let sourceType = $state('ha_camera');
  let source = $state('');
  let roi = $state<Roi | null>(null);
  let kind = $state<SensorKind>('single_state');
  let states = $state<{ name: string; color?: string }[]>([{ name: 'Open' }, { name: 'Closed' }]);
  let classes = $state<string[]>([...(app.config?.object_defaults.classes ?? [])]);
  let reading = $state<ReadingSettings>({ ...app.config!.reading_defaults });
  let previewUrl = $state<string | null>(null);
  let previewError = $state('');
  let saving = $state(false);
  // Start from the backend defaults (GET /config), the same values a sensor gets when this step is skipped.
  let interval_s = $state(app.config?.sensor_defaults.interval_s ?? 10);
  let triggers = $state<Triggers>(structuredClone($state.snapshot(app.config!.trigger_defaults)));
  let publish = $state(app.config?.publish_default ?? true);
  // The test in the "Detect" step of an object or reading sensor (it tests a fresh frame when shown).
  let detectStep = $state<{ test: () => void }>();
  // The camera's light (picked in step 1) is on while the region and the test show live frames.
  let lightReady = $state(false);
  $effect(() => {
    if (!lightReady) return;
    // Bright now: take the frame again, in the light.
    untrack(() => {
      if (step === 1) loadPreview();
      else if (step === 2) detectStep?.test();
    });
  });

  // The entity ID Home Assistant gives the new sensor (it names it after the device).
  const entity = $derived(haSlug(name));
  const stateKeys = $derived(states.filter((s) => s.name.trim()).map((s) => slugify(s.name, 'state')));

  const statesValid = $derived(
    states.length >= 2 && states.every((s) => s.name.trim()) && new Set(stateKeys).size === stateKeys.length,
  );
  const detectValid = $derived(kind === 'objects' ? classes.length > 0 : kind === 'reading' ? true : statesValid);

  const KIND_DEFAULTS = {
    single_state: () => app.config!.sensor_defaults,
    objects: () => app.config!.object_sensor_defaults,
    reading: () => app.config!.reading_sensor_defaults,
  } as const;

  function chooseKind(value: SensorKind) {
    kind = value;
    interval_s = KIND_DEFAULTS[value]().interval_s; // e.g. meters change slowly: 30 s
  }

  const canNext = $derived(
    step === 0
      ? name.trim().length > 0 && source.trim().length > 0
      : step === 1
        ? previewUrl !== null
        : step === 2
          ? detectValid
          : true,
  );
  function loadPreview() {
    previewError = '';
    previewUrl = null;
    const url = api.previewUrl(sourceType, source);
    const img = new Image();
    img.onload = () => (previewUrl = url);
    img.onerror = () => (previewError = 'Could not get a frame from this source. Check the camera or URL.');
    img.src = url;
  }

  function goto(target: number) {
    if (target === 1 && step === 0) loadPreview();
    step = target;
  }

  async function create() {
    saving = true;
    try {
      const learned = kind === 'single_state';
      const sensor = await api.createSensor({
        name: name.trim(),
        kind,
        source_type: sourceType,
        source: source.trim(),
        roi,
        states: learned ? states.map((s) => ({ name: s.name.trim(), color: s.color })) : [],
        ...(kind === 'objects' ? { objects: { ...app.config!.object_defaults, classes } } : {}),
        ...(kind === 'reading' ? { reading } : {}),
        interval_s,
        triggers,
        publish,
      });
      toast(learned ? `${sensor.name} created — now label some frames` : `${sensor.name} created`);
      go(paths.sensor(sensor.id, learned ? 'label' : 'live'));
    } catch (err) {
      toastError(err);
    } finally {
      saving = false;
    }
  }
</script>

<div class="page">
  <div class="wizard card">
    <aside>
      <div class="col" style="gap:4px">
        <span class="eyebrow">New sensor</span>
        <span class="title">{name || 'Untitled'}</span>
      </div>
      <ol>
        {#each STEPS as s, i (i)}
          <li>
            <button class:current={i === step} disabled={i > step && !canNext} onclick={() => i < step && goto(i)}>
              <span class="num" class:done={i <= step}>{#if i < step}<Icon name="check" size={13} strokeWidth={2.4} />{:else}{i + 1}{/if}</span>
              <span class="col" style="gap:2px"><strong>{s.title}</strong><span class="xsmall muted sub">{s.sub}</span></span>
            </button>
          </li>
        {/each}
      </ol>
      <span class="spacer"></span>
      <p class="xsmall faint hint">
        You can change everything later.
        {kind === 'single_state' ? 'Labelling starts right after you create the sensor.' : 'This sensor works right away.'}
      </p>
    </aside>

    <section class="col content">
      {#if (step === 1 || step === 2) && triggers.light_entity}
        <LightHold entity={triggers.light_entity} delay={triggers.light_delay_s} bind:ready={lightReady} />
      {/if}
      {#if step === 0}
        <div class="col" style="gap:6px">
          <h2>Name it and pick a camera</h2>
          <p class="muted">Any camera in Home Assistant works — Frigate, ESP32-CAM, Reolink, generic streams.</p>
        </div>
        <label class="field" style="max-width:420px">
          Sensor name
          <input class="input" bind:value={name} placeholder="Garage door" />
        </label>
        <SourcePicker bind:sourceType bind:source />
        <div style="max-width:640px"><LightEditor bind:triggers /></div>
      {:else if step === 1}
        <div class="row wrap">
          <div class="col" style="gap:6px">
            <h2>Draw the region to watch</h2>
            <p class="muted">Drag a box around what to watch, then shape it if needed. The AI only looks inside it — that makes it much more accurate.</p>
          </div>
          <span class="spacer"></span>
          <button class="btn sm" onclick={loadPreview}><Icon name="refresh" size={14} /> New frame</button>
          {#if isPolygon(roi)}
            <button class="btn sm" onclick={() => roi && (roi = toRectangle(roi))}>Reset to rectangle</button>
          {/if}
          <button class="btn sm" onclick={() => (roi = null)}><Icon name="frame" size={14} /> Use full frame</button>
        </div>
        {#if previewError}
          <div class="notice danger"><Icon name="alert" />{previewError}</div>
        {:else}
          <div class="frame">
            <RoiEditor src={previewUrl} bind:roi editable />
          </div>
          <p class="small muted">Tip: for a door or gate, leave a small margin and include the parts that change; for objects, cover the area where they
            appear; for a number, draw tightly around the digits (objects and numbers can be fine-tuned in the next step).</p>
        {/if}
      {:else if step === 2}
        <div class="col" style="gap:6px">
          <h2>What should this sensor detect?</h2>
          <p class="muted">This can not be changed later — create another sensor for the other kind.</p>
        </div>
        <div class="kinds" role="radiogroup" aria-label="Sensor kind">
          {#each Object.entries(SENSOR_KIND_INFO) as [value, info] (value)}
            <button
              class="kind"
              class:selected={kind === value}
              role="radio"
              aria-checked={kind === value}
              onclick={() => chooseKind(value as SensorKind)}
            >
              <span class="radio" aria-hidden="true"></span>
              <span class="col" style="gap:4px">
                <strong>{info.title}</strong>
                <span class="small muted">{info.text}</span>
                <span class="xsmall faint">{info.example}</span>
              </span>
            </button>
          {/each}
        </div>

        {#if kind === 'reading'}
          <ReadingStep bind:this={detectStep} {sourceType} {source} {entity} bind:roi bind:reading />
        {:else if kind === 'objects'}
          <ObjectsStep bind:this={detectStep} {sourceType} {source} {name} bind:roi bind:classes />
        {:else}
          <StatesStep {entity} {stateKeys} bind:states />
        {/if}
      {:else}
        <div class="col" style="gap:6px">
          <h2>When should it check the camera?</h2>
          <p class="muted">
            Optional — the defaults work. Adding a motion sensor or the garage opener makes the sensor react faster and
            check the camera less often. You can change this later under the sensor's Settings.
          </p>
        </div>
        <div style="max-width:640px"><TriggersEditor bind:triggers bind:interval_s /></div>
        <div class="card pad" style="max-width:640px"><PublishSwitch bind:publish /></div>
      {/if}

      <span class="spacer"></span>
      <footer class="row">
        {#if step > 0}
          <button class="btn" onclick={() => (step -= 1)}>Back</button>
        {:else}
          <a class="btn" href={href(paths.dashboard())}>Cancel</a>
        {/if}
        <span class="spacer"></span>
        {#if step < STEPS.length - 1}
          <button class="btn primary" disabled={!canNext} onclick={() => goto(step + 1)}>Next</button>
        {:else}
          <button class="btn primary" disabled={!detectValid || saving} onclick={create}>
            {saving ? 'Creating…' : kind === 'single_state' ? 'Create sensor and start labelling' : 'Create sensor'}
          </button>
        {/if}
      </footer>
    </section>
  </div>
</div>

<style>
  .wizard {
    display: flex;
    min-height: 680px;
    overflow: hidden;
  }
  aside {
    width: 280px;
    flex-shrink: 0;
    background: var(--c-sunken);
    border-right: 1px solid var(--c-border);
    padding: 28px 24px;
    display: flex;
    flex-direction: column;
    gap: 28px;
  }
  .eyebrow {
    font-size: var(--fs-sm);
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--c-faint);
  }
  .title {
    font: 600 20px var(--font-display);
  }
  ol {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  ol button {
    width: 100%;
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: 10px;
    border-radius: var(--radius-md);
    background: transparent;
    border: none;
    color: var(--c-text);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  ol button.current {
    background: var(--c-surface-3);
  }
  ol button:disabled {
    cursor: default;
  }
  .num {
    width: 28px;
    height: 28px;
    flex-shrink: 0;
    border-radius: var(--radius-pill);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: var(--fs-md);
    font-weight: 600;
    background: var(--c-surface-3);
    color: var(--c-muted);
  }
  .num.done {
    background: var(--c-accent);
    color: var(--c-accent-ink);
  }
  .content {
    flex-grow: 1;
    padding: 28px 32px;
    gap: var(--space-5);
    min-width: 0;
  }
  .frame {
    max-width: 900px;
    border-radius: var(--radius-lg);
    overflow: hidden;
  }
  footer {
    padding-top: var(--space-3);
    border-top: 1px solid var(--c-border);
  }
  .kinds {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: var(--space-3);
    max-width: 960px;
  }
  .kind {
    display: flex;
    align-items: flex-start;
    gap: var(--space-3);
    padding: var(--space-4);
    border-radius: var(--radius-lg);
    border: 1px solid var(--c-border-strong);
    background: var(--c-surface);
    color: var(--c-text);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .kind.selected {
    border-color: var(--c-accent);
    box-shadow: 0 0 0 1px var(--c-accent);
  }
  .radio {
    width: 18px;
    height: 18px;
    margin-top: 2px;
    flex-shrink: 0;
    border-radius: 50%;
    border: 2px solid var(--c-border-strong);
  }
  .kind.selected .radio {
    border: 5px solid var(--c-accent);
  }
  @media (max-width: 600px) {
    .kinds {
      grid-template-columns: minmax(0, 1fr);
    }
  }
  @media (max-width: 900px) {
    .wizard {
      flex-direction: column;
    }
    .wizard {
      min-height: 0;
    }
    aside {
      width: auto;
      border-right: none;
      border-bottom: 1px solid var(--c-border);
      padding: var(--space-4);
      gap: var(--space-3);
    }
    /* Steps become one compact row so the form starts on the first screen. */
    ol {
      flex-direction: row;
      gap: 4px;
    }
    li {
      flex: 1 1 0;
      min-width: 0;
    }
    ol button {
      flex-direction: column;
      gap: 4px;
      padding: 8px 4px;
      text-align: center;
      font-size: var(--fs-sm);
    }
    ol button .col {
      align-items: center;
    }
    .sub,
    .hint {
      display: none;
    }
  }
  @media (max-width: 600px) {
    .content {
      padding: var(--space-5) var(--space-4);
    }
  }
</style>
