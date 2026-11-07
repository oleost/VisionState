<script lang="ts">
  // The new sensor wizard: camera, what to detect and where (one component per kind in wizard/),
  // and when to check. Nothing is stored until "Create sensor".
  import { untrack } from 'svelte';
  import { api } from '../lib/api';
  import { app, toast, toastError } from '../lib/app.svelte';
  import Icon from '../lib/components/Icon.svelte';
  import LightEditor from '../lib/components/LightEditor.svelte';
  import LightHold from '../lib/components/LightHold.svelte';
  import SourcePicker from '../lib/components/SourcePicker.svelte';
  import TriggersEditor from '../lib/components/TriggersEditor.svelte';
  import { haSlug, slugify } from '../lib/format';
  import { takeNewSensorPrefill } from '../lib/prefill.svelte';
  import { go, href, paths } from '../lib/router.svelte';
  import type { ReadingSettings, Roi, SensorKind, SoundSettings, Triggers } from '../lib/types';
  import { HA_MODES, LOW_STREAM_NAME, SENSOR_KIND_INFO, SENSOR_MODE_INFO, SMALL_FRAME_PX } from '../lib/ui';
  import CameraPreview from './wizard/CameraPreview.svelte';
  import ObjectsStep from './wizard/ObjectsStep.svelte';
  import ReadingStep from './wizard/ReadingStep.svelte';
  import SoundStep from './wizard/SoundStep.svelte';
  import StatesStep from './wizard/StatesStep.svelte';

  const PICTURE_STEPS = [
    { title: 'Camera', sub: 'Name and image source' },
    { title: 'Detect', sub: 'States, objects or a number, and where' },
    { title: 'Checks', sub: 'When to look (optional)' },
  ];
  const SOUND_STEPS = [
    { title: 'Stream', sub: 'Name and sound source' },
    { title: 'Listen', sub: 'What to hear' },
  ];

  // Look at the picture (states, objects, a number) or listen (a sound sensor).
  let mode = $state<'picture' | 'sound'>('picture');
  const STEPS = $derived(mode === 'sound' ? SOUND_STEPS : PICTURE_STEPS);
  let sound = $state<SoundSettings>(structuredClone($state.snapshot(app.config!.sound_defaults)));
  // From the ESP32 guide: the device's name and address, as if picked here.
  const given = takeNewSensorPrefill();
  if (given) mode = given.mode;
  let streamType = $state(given?.mode === 'sound' ? given.sourceType : 'ha_camera'); // a Home Assistant camera, or a stream address ('rtsp')
  let streamSource = $state(given?.mode === 'sound' ? given.source : '');

  let step = $state(0);
  let name = $state(given?.name ?? '');
  let sourceType = $state(given?.mode === 'picture' ? given.sourceType : 'ha_camera');
  let source = $state(given?.mode === 'picture' ? given.source : '');
  let roi = $state<Roi | null>(null);
  let kind = $state<SensorKind>('single_state');
  let states = $state<{ name: string; color?: string }[]>([{ name: 'Open' }, { name: 'Closed' }]);
  let classes = $state<string[]>([...(app.config?.object_defaults.classes ?? [])]);
  let faces = $state(false); // recognise people by their face (objects with Person)
  let reading = $state<ReadingSettings>({ ...app.config!.reading_defaults });
  let previewUrl = $state<string | null>(null);
  let previewSize = $state<[number, number] | null>(null);
  // A small picture, or a source named like a sub-stream: maybe the camera's low-resolution stream.
  // An ESP32 from the guide has one picture size, its own: no word about a camera's low-resolution stream.
  const fromGuide = (s: string) => given?.mode === 'picture' && s === given.source;
  const lowStream = $derived(
    !fromGuide(source) &&
      previewSize !== null &&
      (Math.max(...previewSize) < SMALL_FRAME_PX || LOW_STREAM_NAME.test(source)),
  );
  // The guide's device has a flash: offered as the light (one tap), never switched on unasked.
  let lightKey = $state(0);
  function useFlash() {
    triggers.light_entity = given!.light!;
    lightKey++;
  }
  let previewError = $state('');
  let previewBusy = $state(false);
  let previewLoad = 0; // the load that counts: a slow answer from the camera picked before is dropped
  let previewPending: Promise<boolean> | null = null;
  let saving = $state(false);
  // Start from the backend defaults (GET /config), the same values a sensor gets when this step is skipped.
  let interval_s = $state(app.config?.sensor_defaults.interval_s ?? 10);
  let triggers = $state<Triggers>(structuredClone($state.snapshot(app.config!.trigger_defaults)));
  let notInHa = $state(false);
  // The test in the "Detect" step of an object or reading sensor (it tests a fresh frame when shown).
  let detectStep = $state<{ test: () => void }>();
  // The camera's light (picked with the camera) is on while its picture, the region and the test are shown.
  let lightReady = $state(false);
  $effect(() => {
    if (!lightReady) return;
    // Bright now: take the frame again, in the light.
    untrack(() => {
      if (previewUrl || previewPending) loadPreview();
      if (step === 1) detectStep?.test();
    });
  });
  // Another camera: its picture at once. A URL is tried when it has been typed (SourcePicker's ondone).
  $effect(() => {
    const [picture, type, chosen] = [mode === 'picture', sourceType, source];
    untrack(() => {
      previewLoad++;
      previewPending = null;
      previewBusy = false;
      previewUrl = null;
      previewSize = null;
      previewError = '';
      // A Home Assistant camera at once; an address once typed — or at once, from the ESP32 guide.
      if (picture && chosen && (type === 'ha_camera' || fromGuide(chosen))) loadPreview();
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

  function chooseKind(value: Exclude<SensorKind, 'sound'>) {
    kind = value;
    interval_s = KIND_DEFAULTS[value]().interval_s; // e.g. meters change slowly: 30 s
  }

  const canNext = $derived(
    mode === 'sound'
      ? step === 0
        ? name.trim().length > 0 && streamSource.trim().length > 0
        : true
      : step === 0
      ? name.trim().length > 0 && source.trim().length > 0 && !previewError
      : step === 1
        ? detectValid
        : true,
  );
  /** A fresh frame; the one shown stays until it has come, so the page does not jump. */
  function loadPreview(): Promise<boolean> {
    const mine = ++previewLoad;
    previewError = '';
    previewBusy = true;
    const url = api.previewUrl(sourceType, source);
    const pending = new Promise<boolean>((resolve) => {
      const done = (ok: boolean) => {
        if (mine !== previewLoad) return resolve(false); // another camera was picked meanwhile
        previewBusy = false;
        previewPending = null;
        resolve(ok);
      };
      const img = new Image();
      img.onload = () => {
        if (mine === previewLoad) {
          previewUrl = url;
          previewSize = [img.naturalWidth, img.naturalHeight];
        }
        done(true);
      };
      img.onerror = () => {
        if (mine === previewLoad) previewError = 'Could not get a frame from this source. Check the camera or URL.';
        done(false);
      };
      img.src = url;
    });
    previewPending = pending;
    return pending;
  }

  function urlTyped() {
    if (source.trim() && !previewUrl && !previewPending) loadPreview();
  }

  async function goto(target: number) {
    if (target === 1 && step === 0 && mode === 'picture' && !previewUrl) {
      // A URL just typed: its picture comes first. Stay when it needs a word about its resolution.
      if (!(await (previewPending ?? loadPreview())) || lowStream) return;
    }
    step = target;
  }

  async function createSound() {
    saving = true;
    try {
      const sensor = await api.createSensor({
        name: name.trim(),
        kind: 'sound',
        source_type: streamType,
        source: streamSource.trim(),
        roi: null,
        states: [],
        sound,
        in_ha: !notInHa,
      });
      toast(`${sensor.name} created`);
      go(paths.sensor(sensor.id, 'live'));
    } catch (err) {
      toastError(err);
    } finally {
      saving = false;
    }
  }

  async function create() {
    if (mode === 'sound') return createSound();
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
        ...(kind === 'objects'
          ? {
              objects: {
                ...app.config!.object_defaults,
                classes,
                faces: { ...app.config!.face_defaults, enabled: faces && classes.includes('person') },
              },
            }
          : {}),
        ...(kind === 'reading' ? { reading } : {}),
        interval_s,
        triggers,
        in_ha: !notInHa,
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

{#snippet notInHaChoice()}
  <label class="card pad not-in-ha" style="max-width:640px">
    <input type="checkbox" bind:checked={notInHa} aria-label={HA_MODES.none.label} />
    <span class="col" style="gap:2px">
      <strong class="small">{HA_MODES.none.label}</strong>
      <span class="xsmall faint">{HA_MODES.none.help} You can change this on its Home Assistant tab.</span>
    </span>
  </label>
{/snippet}

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
        {mode === 'picture' && kind === 'single_state' ? 'Labelling starts right after you create the sensor.' : 'This sensor works right away.'}
      </p>
    </aside>

    <section class="col content">
      <!-- One hold for the camera's picture and the test, so the light does not blink between the
           steps. It sits above the picture on the first step and on top of the next (CSS order). -->
      {#if mode === 'picture' && step <= 1 && triggers.light_entity && source.trim()}
        <div class="hold" class:first={step === 1}>
          <LightHold entity={triggers.light_entity} delay={triggers.light_delay_s} bind:ready={lightReady} />
        </div>
      {/if}
      {#if step === 0}
        <div class="modes" role="radiogroup" aria-label="What the sensor does">
          {#each Object.entries(SENSOR_MODE_INFO) as [value, info] (value)}
            <button
              class="kind mode"
              class:selected={mode === value}
              role="radio"
              aria-checked={mode === value}
              onclick={() => (mode = value as 'picture' | 'sound')}
            >
              <span class="radio" aria-hidden="true"></span>
              <span class="col" style="gap:4px">
                <strong class="row" style="gap:6px"><Icon name={info.icon} size={16} />{info.title}</strong>
                <span class="small muted">{info.text}</span>
              </span>
            </button>
          {/each}
        </div>
        {#if mode === 'sound'}
          <div class="col" style="gap:6px">
            <h2>Name it and pick a stream with sound</h2>
            <p class="muted">A camera with a microphone, or a microphone of its own (an ESP32 with a microphone, sending its sound).</p>
          </div>
          <label class="field" style="max-width:420px">
            Sensor name
            <input class="input" bind:value={name} placeholder="Hallway" />
          </label>
          <SourcePicker sound bind:sourceType={streamType} bind:source={streamSource} />
        {:else}
          <div class="col" style="gap:6px">
            <h2>Name it and pick a camera</h2>
            <p class="muted">Any camera in Home Assistant works — Frigate, ESP32-CAM, Reolink, generic streams.</p>
          </div>
          <label class="field" style="max-width:420px">
            Sensor name
            <input class="input" bind:value={name} placeholder="Garage door" />
          </label>
          <SourcePicker bind:sourceType bind:source ondone={urlTyped} />
          {#if given?.light && triggers.light_entity !== given.light}
            <p class="small" data-testid="esp32-flash">
              The device has a flash (<span class="mono">{given.light}</span>): it can light the picture for checks in the
              dark. <button class="btn sm" onclick={useFlash}>Use the flash</button>
            </p>
          {/if}
          <div style="max-width:640px">{#key lightKey}<LightEditor bind:triggers />{/key}</div>
          <div class="after-hold">
            <CameraPreview
              url={previewUrl}
              size={previewSize}
              busy={previewBusy}
              error={previewError}
              low={lowStream}
              onretry={loadPreview}
            />
          </div>
        {/if}
      {:else if mode === 'sound'}
        <div class="col" style="gap:6px">
          <h2>What should it listen for?</h2>
          <p class="muted">
            Each sound becomes an on/off sensor. Sounds of your own — your doorbell, the washing machine — are taught once
            the sensor runs.
          </p>
        </div>
        <SoundStep sourceType={streamType} source={streamSource.trim()} name={name.trim()} bind:sound />
        {@render notInHaChoice()}
      {:else if step === 1}
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
              onclick={() => chooseKind(value as Exclude<SensorKind, 'sound'>)}
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
          <ReadingStep bind:this={detectStep} {sourceType} {source} {entity} bind:roi bind:reading frame={previewUrl} />
        {:else if kind === 'objects'}
          <ObjectsStep bind:this={detectStep} {sourceType} {source} {name} bind:roi bind:classes bind:faces frame={previewUrl} />
        {:else}
          <StatesStep {entity} {stateKeys} bind:states bind:roi frame={previewUrl} onnewframe={loadPreview} />
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
        {@render notInHaChoice()}
      {/if}

      <span class="spacer after-hold"></span>
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
          <button
            class="btn primary"
            disabled={(mode === 'picture' ? !detectValid : !sound.sounds.length) || saving}
            onclick={create}
          >
            {saving ? 'Creating…' : mode === 'picture' && kind === 'single_state' ? 'Create sensor and start labelling' : 'Create sensor'}
          </button>
        {/if}
      </footer>
    </section>
  </div>
</div>

<style>
  .not-in-ha {
    display: flex;
    gap: var(--space-2);
    align-items: flex-start;
  }
  .not-in-ha input {
    margin-top: 3px;
  }
  .wizard {
    display: flex;
    min-height: 680px; /* no overflow: hidden, it would cut off the lists of the pickers */
  }
  aside {
    width: 280px;
    flex-shrink: 0;
    background: var(--c-sunken);
    border-radius: var(--radius-lg) 0 0 var(--radius-lg);
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
  .hold {
    order: 1;
    max-width: 640px;
  }
  .hold.first {
    order: -1;
    max-width: none;
  }
  .after-hold {
    order: 2;
  }
  footer {
    order: 3;
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
  .modes {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: var(--space-3);
    max-width: 760px;
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
    .kinds,
    .modes {
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
      border-radius: var(--radius-lg) var(--radius-lg) 0 0;
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
