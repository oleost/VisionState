<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { api } from '../../lib/api';
  import { app, toast, toastError } from '../../lib/app.svelte';
  import AdviceCard from '../../lib/components/AdviceCard.svelte';
  import ConfirmButton from '../../lib/components/ConfirmButton.svelte';
  import CounterDialsSwitch from '../../lib/components/CounterDialsSwitch.svelte';
  import DialsEditor from '../../lib/components/DialsEditor.svelte';
  import RegionButtons from '../../lib/components/RegionButtons.svelte';
  import Icon from '../../lib/components/Icon.svelte';
  import LightEditor from '../../lib/components/LightEditor.svelte';
  import LiveFrame from '../../lib/components/LiveFrame.svelte';
  import ObjectParams from '../../lib/components/ObjectParams.svelte';
  import LabelPicker from '../../lib/components/LabelPicker.svelte';
  import DigitCells from '../../lib/components/DigitCells.svelte';
  import ExportButton from '../../lib/components/ExportButton.svelte';
  import FacesCard from '../../lib/components/FacesCard.svelte';
  import PlatesCard from '../../lib/components/PlatesCard.svelte';
  import ReadingEditor from '../../lib/components/ReadingEditor.svelte';
  import ReadingParams from '../../lib/components/ReadingParams.svelte';
  import ReviewRulesEditor from '../../lib/components/ReviewRulesEditor.svelte';
  import SensorParams from '../../lib/components/SensorParams.svelte';
  import SoundEditor from '../../lib/components/sound/SoundEditor.svelte';
  import SourcePicker from '../../lib/components/SourcePicker.svelte';
  import StraightenSwitch from '../../lib/components/StraightenSwitch.svelte';
  import StatesEditor from '../../lib/components/StatesEditor.svelte';
  import TeachLimitsEditor from '../../lib/components/TeachLimitsEditor.svelte';
  import TriggersEditor from '../../lib/components/TriggersEditor.svelte';
  import { isObjectSensor, objectName, objectPicker } from '../../lib/objects';
  import { extraDigits, isReadingSensor, valueDecimals } from '../../lib/reading';
  import { toPoints, toRectangle } from '../../lib/roi';
  import { go, href, paths } from '../../lib/router.svelte';
  import { isSoundSensor } from '../../lib/sound';
  import HomeAssistantTab from './HomeAssistantTab.svelte';
  import type { ReadPreview, ReviewRules, Roi, Sensor, SoundSettings, TeachLimitsInfo, TeachOverrides } from '../../lib/types';
  import { REDACTED_MARK, SETTINGS_SECTION_LABELS, SETTINGS_SECTIONS, type SettingsSection } from '../../lib/ui';

  // A sensor's Settings in sections (SETTINGS_SECTIONS): on a phone a list of them, each saying what
  // is set, and one open at a time; on a wider screen a menu beside the open one. The form is one
  // for all sections, so a change in one is kept while another is open; Save and Undo appear at the
  // bottom of the screen once something changed. Home Assistant saves on its own (HomeAssistantTab).
  let {
    sensor,
    section = '',
    onchange,
    onreset,
  }: { sensor: Sensor; section?: SettingsSection | ''; onchange: () => void; onreset: () => void } = $props();

  // Local editable copy; the parent keeps polling the live sensor.
  const initial = untrack(() => $state.snapshot(sensor));
  let name = $state(initial.name);
  let sourceType = $state(initial.source_type);
  let source = $state(initial.source);
  let roi = $state<Roi | null>(initial.roi);
  let states = $state(initial.states.map((s) => ({ key: s.key, name: s.name, color: s.color })));
  let interval_s = $state(initial.interval_s);
  let threshold = $state(initial.threshold);
  let debounce = $state(initial.debounce);
  let triggers = $state(initial.triggers);
  let review = $state(initial.review);
  const objectSensor = isObjectSensor(initial);
  let classes = $state<string[]>(initial.objects?.classes ?? []);
  let clearAfter = $state(initial.objects?.clear_after_s ?? 0);
  let minSize = $state(initial.objects?.min_size ?? 0);
  let useTaught = $state(initial.objects?.use_taught ?? true);
  let teachLimits = $state<TeachOverrides>({ ...(initial.objects?.teach ?? {}) });
  let globalTeach = $state<TeachLimitsInfo | null>(null);
  if (objectSensor) api.teachLimits().then((t) => (globalTeach = t)).catch(toastError);
  // Boxes taught by their look; faces are counted on the Faces card.
  const taughtCount = $derived(sensor.objects?.taught ?? 0); // boxes (faces are under People)
  // Own labels the user made (not the people or cars every sensor shares).
  const taughtSomething = $derived(
    !!sensor.objects && (taughtCount > 0 || sensor.objects.custom.some((l) => !l.face && !l.car)),
  );
  const readingSensor = isReadingSensor(initial);
  const { value: _v, last: _l, has_image: _h, limit: _lim, camera_changed: _cc, ...initialReading } = initial.reading ?? ({} as NonNullable<typeof initial.reading>);
  let reading = $state({ ...app.config!.reading_defaults, ...initialReading });
  const soundSensor = isSoundSensor(initial);
  const { custom: _sc, live: _sl, levels: _sv, line: _sn, analysed: _sa, stream: _ss, ...initialSound } =
    initial.sound ?? ({} as NonNullable<typeof initial.sound>);
  let sound = $state<SoundSettings>({ ...app.config!.sound_defaults, ...initialSound });
  const thresholdsBefore = { ...(initialSound.thresholds ?? {}) };
  const hasVideo = $derived(sensor.sound?.stream ? !!sensor.sound.stream.video : null);

  // A threshold set from the answers (AdviceCard) is saved at once: the form shows it too, so Save
  // does not put the old one back; the faces card, which keeps its own copy, is drawn again.
  let facesVersion = $state(0);
  function adviceApplied(key: string, value: number) {
    if (key === 'threshold') threshold = value;
    else if (key.startsWith('sound:')) {
      sound.thresholds = { ...sound.thresholds, [key.slice('sound:'.length)]: value };
      thresholdsBefore[key.slice('sound:'.length)] = value;
    } else if (key === 'teach:match_similarity') teachLimits = { ...teachLimits, match_similarity: value };
    else if (key === 'faces:match_similarity') facesVersion++;
    // Saved already: not a change waiting for Save.
    const was = saved ? JSON.parse(saved) : null;
    if (was) {
      const sub = key.split(':')[1];
      if (key === 'threshold') was.threshold = value;
      else if (key.startsWith('sound:')) was.sound = { ...was.sound, thresholds: { ...was.sound.thresholds, [sub]: value } };
      else if (key === 'teach:match_similarity') was.objects = { ...was.objects, teach: { ...was.objects.teach, match_similarity: value } };
      saved = JSON.stringify(was);
    }
    onchange();
  }

  /** A sound sensor's settings: a threshold set back to the model's is sent as null (removed). */
  function soundUpdate() {
    const thresholds: Record<string, number | null> = { ...sound.thresholds };
    for (const key of Object.keys(thresholdsBefore)) if (!(key in thresholds)) thresholds[key] = null;
    return { name, source_type: sourceType, source, review, sound: { ...sound, thresholds } };
  }
  // A reading sensor's region straightened: four corners, its top marked (RoiEditor quad).
  const straightened = $derived(readingSensor && reading.straighten);
  $effect(() => {
    if (straightened && roi && toPoints(roi).length > 4) roi = toRectangle(roi);
  });
  // A counter read by a wheel reader: its pointer dials, placed on a fresh frame's meter face (read with
  // the settings being edited, so it shows what they would read).
  const canDial = $derived(readingSensor && reading.display === 'counter' && reading.counter_reader === 'wheels');
  let editing = $state<'counter' | 'dials'>('counter');
  const editingDials = $derived(canDial && editing === 'dials');
  let dialPreview = $state<ReadPreview | null>(null);
  let dialBusy = $state(false);
  let dialError = $state('');
  async function readDials() {
    dialBusy = true;
    dialError = '';
    try {
      dialPreview = await api.previewRead(sourceType, source, roi, $state.snapshot(reading));
    } catch (err) {
      dialError = (err as Error).message;
    } finally {
      dialBusy = false;
    }
  }
  // Each time the picture turns to the dials: the region may have changed since.
  $effect(() => {
    if (editingDials) untrack(readDials);
  });
  const labels = $derived(SETTINGS_SECTION_LABELS[sensor.kind]);
  const sections = $derived(SETTINGS_SECTIONS.filter((id) => labels[id]));
  // No section asked for: the first (a phone shows the list instead, by CSS).
  const shown = $derived<SettingsSection>(
    section && labels[section] ? section : section === 'region' && readingSensor ? 'detect' : 'general',
  );
  const short = (names: string[]) => (names.length > 3 ? `${names.slice(0, 3).join(', ')} +${names.length - 3}` : names.join(', '));
  // What each section holds, in a line under its name in a phone's list.
  const summaries = $derived<Record<SettingsSection, string>>({
    general: [name, sourceType === 'ha_camera' ? source : (app.config?.source_types[sourceType] ?? sourceType), triggers.light_entity ? 'light' : '']
      .filter(Boolean)
      .join(' · '),
    region: roi ? 'Part of the picture' : 'The whole picture',
    detect: readingSensor
      ? `${reading.digits} digits${reading.unit ? ` · ${reading.unit}` : ''}`
      : objectSensor
        ? short(classes.map((c) => objectName(c, sensor.objects?.custom ?? [])))
        : soundSensor
          ? `${sound.sounds.length + (sensor.sound?.custom?.length ?? 0)} sounds`
          : short(states.map((x) => x.name)),
    checks: soundSensor
      ? sound.clips
        ? 'Sounds only just heard are asked about'
        : 'Nothing asked: clips are off'
      : [interval_s > 0 ? `Every ${interval_s} s` : 'No regular check', `threshold ${Math.round(threshold * 100)} %`].join(' · '),
    'home-assistant': !sensor.in_ha
      ? 'Not in Home Assistant'
      : !sensor.publish
        ? 'Values not sent'
        : `${sensor.ha_entities.filter((e) => e.on).length} entities`,
    export: 'To a file, or delete the sensor',
  });
  let globalReview = $state<ReviewRules | null>(null);
  api.reviewRules().then((r) => (globalReview = r)).catch(toastError);
  let saving = $state(false);

  /** What Save sends: everything the form holds. */
  function payload() {
    return soundSensor
      ? soundUpdate()
      : {
          name,
          source_type: sourceType,
          source,
          ...(roi ? { roi } : { clear_roi: true }),
          ...(objectSensor
            ? { objects: { classes, clear_after_s: clearAfter, min_size: minSize, use_taught: useTaught, teach: teachLimits } }
            : readingSensor
              ? { reading }
              : { states, review }),
          interval_s,
          threshold,
          debounce,
          triggers,
        };
  }
  // Changed = differs from what was saved, as the form first showed it (taken once the editors
  // have settled their bound values).
  let saved = $state('');
  onMount(() => {
    saved = JSON.stringify(payload());
  });
  const dirty = $derived(!!saved && JSON.stringify(payload()) !== saved);

  async function save() {
    saving = true;
    try {
      const sent = payload();
      await api.updateSensor(sensor.id, sent);
      saved = JSON.stringify(sent);
      toast('Settings saved');
      onchange();
    } catch (err) {
      toastError(err);
    } finally {
      saving = false;
    }
  }

  async function forgetTaught() {
    try {
      await api.forgetAll(sensor.id);
      toast('Everything taught is forgotten');
      onchange();
    } catch (err) {
      toastError(err);
    }
  }

  async function remove() {
    try {
      await api.deleteSensor(sensor.id);
      toast(`${sensor.name} deleted`);
      go(paths.dashboard());
    } catch (err) {
      toastError(err);
    }
  }
</script>

<div class="settings" class:picked={!!section}>
  <nav class="sections" aria-label="Settings sections">
    {#each sections as id (id)}
      <a
        href={href(paths.sensorSettings(sensor.id, id))}
        class:on={id === shown}
        class:danger={id === 'export'}
        aria-current={id === shown ? 'page' : undefined}
      >
        <span class="col">
          <strong>{labels[id]}</strong>
          <span class="summary">{summaries[id]}</span>
        </span>
        <Icon name="chevron" size={16} />
      </a>
    {/each}
  </nav>

  <div class="content col">
    <a class="back" href={href(paths.sensorSettings(sensor.id))}><Icon name="back" size={14} /> Settings</a>
    <h2>{labels[shown]}</h2>
    {#if shown === 'general'}
    {#if source.includes(REDACTED_MARK)}
      <div class="notice warn">
        The camera password was removed when this sensor was exported. Enter the full URL again below and save.
      </div>
    {/if}
    <section class="card pad col">
      <label class="field">Name <input class="input" bind:value={name} /></label>
      {#if soundSensor}
        <SourcePicker sound bind:sourceType bind:source />
      {:else}
        <SourcePicker bind:sourceType bind:source />
        <LightEditor bind:triggers lightError={sensor.live.light_error} />
        {#if sensor.camera}
          {@const uses = app.config?.camera_uses ?? {}}
          {@const byKind = sensor.camera.by_kind}
          <label class="field" data-testid="camera-use">
            Camera set-up
            <select class="input" bind:value={triggers.camera_use} aria-label="Camera set-up">
              <option value="">Automatic{byKind ? `: ${uses[byKind] ?? byKind}` : ''}</option>
              {#each Object.entries(uses) as [key, label] (key)}<option value={key}>{label}</option>{/each}
            </select>
            <span class="xsmall muted">
              A VisionState ESP32 camera: before this sensor's pictures it is set up for what the sensor looks at (picture
              size, compression, exposure). Sensors on one camera share the most demanding.
              {#if sensor.camera.now}Now: {uses[sensor.camera.now] ?? sensor.camera.now}.{/if}
            </span>
          </label>
        {/if}
      {/if}
    </section>

    {:else if shown === 'region' || shown === 'detect'}
      {#if shown === 'region' || readingSensor}
    {#if !soundSensor}
      <section class="card pad col">
        <!-- The section's heading names it; the buttons keep to the right. -->
        <div class="card-title">
          <span></span>
          <span class="row wrap region-actions" style="gap:8px">
            {#if editingDials}
              <button class="btn sm" onclick={readDials} disabled={dialBusy}>
                <Icon name="refresh" size={14} /> {dialBusy ? 'Reading…' : 'Read now'}
              </button>
            {:else}
              <RegionButtons bind:roi quad={straightened} />
            {/if}
          </span>
        </div>
        {#if canDial}<CounterDialsSwitch bind:editing dials={reading.dials.length} />{/if}
        {#if editingDials}
          <div class="col" data-testid="dials-card">
            <p class="small muted">
              The small dials beside the wheels (×0.1, ×0.01 …): each one more digit, read with the wheels. The face is
              straightened the way the counter is read.
            </p>
            {#if dialError}<p class="notice danger small">{dialError}</p>{/if}
            <DialsEditor preview={dialPreview} bind:dials={reading.dials} decimals={reading.decimals} />
            {#if dialPreview?.value}
              <span class="small">
                Read <strong class="mono">{dialPreview.value}</strong>{dialPreview.dials.length !== reading.dials.length ? ' before your last change' : ''}
              </span>
            {/if}
          </div>
        {:else}
        {#if readingSensor}<StraightenSwitch bind:checked={reading.straighten} counter={reading.display === 'counter'} />{/if}
        <p class="small muted">
          {straightened
            ? 'Put its four corners on the corners of the display or counter, the edge marked Top at its top; it is read upright.'
            : 'Drag to draw a new box, move it, or shape it with corners.'}
          {straightened ? '' : objectSensor
            ? 'An object counts when it stands inside it (the bottom of its box).'
            : readingSensor
              ? reading.display === 'counter'
                ? 'Cover the wheels from the first to the last, so each field holds one wheel. A little room above and below is fine.'
                : 'Draw it tightly around the digits only — no labels or units.'
              : 'Changing it retrains the model.'}
        </p>
        <div class="frame">
          <LiveFrame
            sensorId={sensor.id}
            bind:roi
            editable
            quad={straightened}
            showLive={false}
            interval={10_000}
            light={triggers.light_entity ? { entity: triggers.light_entity, delay: triggers.light_delay_s } : null}
          >
            {#if readingSensor && reading.display === 'counter'}<DigitCells {roi} digits={reading.digits} whole={reading.counter_reader === 'wheels'} {straightened} />{/if}
          </LiveFrame>
        </div>
        {/if}
      </section>
    {/if}
      {/if}
      {#if shown === 'detect'}
    {#if readingSensor}
      <section class="card pad col">
        <ReadingEditor bind:value={reading} seen={sensor.reading?.last?.text} />
      </section>
    {:else if objectSensor}
      <section class="card pad col">
        <p class="small muted">Each object gets an on/off sensor and a count in Home Assistant.</p>
        <LabelPicker bind:selected={classes} {...objectPicker()} />
      </section>
      {#key facesVersion}<FacesCard {sensor} {onchange} />{/key}
      <PlatesCard {sensor} {onchange} />
      {#if taughtSomething}
        <section class="card pad col">
          <h3>What you taught</h3>
          <label class="check taught">
            <input type="checkbox" bind:checked={useTaught} aria-label="Use what you taught" />
            <span class="col" style="gap:2px">
              <strong class="small">Use what you taught</strong>
              <span class="xsmall faint">
                {#if useTaught}
                  Boxes are compared with the {taughtCount} {taughtCount === 1 ? 'box' : 'boxes'} you taught
                  (<a href={href(paths.sensor(sensor.id, 'dataset'))}>see them</a>).
                {:else}
                  Off: the AI alone decides. What you taught is kept for when you turn it on again; own labels stay off.
                {/if}
              </span>
            </span>
          </label>
          <div class="row">
            <ConfirmButton class="btn sm danger" onconfirm={forgetTaught} confirmLabel="Tap again to forget everything taught">
              <Icon name="trash" size={14} /> Forget all
            </ConfirmButton>
            <span class="xsmall faint">Every taught box, own label and person, with their entities.</span>
          </div>
          {#if globalTeach}
            <div class="col teach-limits" data-testid="sensor-teach-limits">
              <strong class="small">Limits for this sensor</strong>
              <TeachLimitsEditor bind:value={teachLimits} inherited={globalTeach.effective} inheritedFrom="the global value" />
            </div>
          {/if}
        </section>
      {/if}
    {:else if soundSensor}
      <section class="card pad col">
        <SoundEditor bind:value={sound} own={sensor.sound?.custom ?? []} {hasVideo} />
      </section>
    {:else}
      <section class="card pad col">
        <p class="small muted">Removing a state also removes its labels. Renaming keeps the Home Assistant value.</p>
        <StatesEditor bind:states />
      </section>
    {/if}

      {/if}
    {:else if shown === 'checks'}
    <AdviceCard {sensor} onapply={adviceApplied} />
    {#if !soundSensor}
    <section class="card pad col">
      <h3>When to check</h3>
      <TriggersEditor bind:triggers bind:interval_s liveScore={sensor.live.change_score} />
    </section>

    <section class="card pad col">
      <h3>Sensor output</h3>
      {#if objectSensor}
        <ObjectParams bind:threshold bind:debounce bind:clearAfter bind:minSize />
      {:else if readingSensor}
        <ReadingParams
          bind:threshold
          bind:debounce
          bind:maxStep={reading.max_step}
          bind:changeLimit={reading.change_limit}
          bind:maxRate={reading.max_rate}
          limit={sensor.reading?.limit ?? null}
          bind:spotRate={reading.spot_rate}
          bind:rateWindow={reading.rate_window_min}
          mode={reading.mode}
          unit={reading.unit}
          decimals={valueDecimals(reading)}
          hasDials={extraDigits(reading) > 0}
          intervalS={interval_s}
        />
      {:else}
        <SensorParams bind:threshold bind:debounce />
      {/if}
    </section>
    {/if}

    {#if !objectSensor && !readingSensor}
      <section class="card pad col">
        {#if !soundSensor}<h3>Review</h3>{/if}<!-- a sound sensor: the section is called Review -->
        {#if soundSensor}
          <p class="small muted">With clips on, a sound that was only just heard is asked about: listen, and answer.</p>
        {/if}
        {#if globalReview}
          {#if soundSensor}
            <ReviewRulesEditor bind:value={review} inherited={globalReview} threshold={null} fields={['cooldown_s']} />
          {:else}
            <ReviewRulesEditor bind:value={review} inherited={globalReview} {threshold} />
          {/if}
        {/if}
      </section>
    {/if}

    {:else if shown === 'home-assistant'}
      <HomeAssistantTab {sensor} {onchange} />
    {:else if shown === 'export'}
      <section class="card pad col">
        <h3>Export</h3>
        <p class="small muted">A file with this sensor's settings and what it was taught, to import on another VisionState.</p>
        <span><ExportButton {sensor} /></span>
      </section>
      <section class="card pad col">
        <h3>Delete</h3>
        <p class="small muted">The sensor with its images, history and what it was taught, and its entities in Home Assistant.</p>
        <span>
          <ConfirmButton onconfirm={remove} confirmLabel="Tap again to delete everything">
            <Icon name="trash" size={14} /> Delete sensor
          </ConfirmButton>
        </span>
      </section>
    {/if}
    {#if dirty}
      <div class="savebar">
        <span class="small muted">Unsaved changes</span>
        <span class="spacer"></span>
        <button class="btn" onclick={onreset}>Undo</button>
        <button class="btn primary" disabled={saving || (objectSensor && !classes.length) || (soundSensor && (!source.trim() || !(sound.sounds.length || sensor.sound?.custom?.length)))} onclick={save}>{saving ? 'Saving…' : 'Save changes'}</button>
      </div>
    {/if}
  </div>
</div>

<style>
  .taught {
    align-items: flex-start;
  }
  .teach-limits {
    gap: var(--space-3);
    padding-top: var(--space-3);
    border-top: 1px solid var(--c-border);
  }
  .taught input {
    margin-top: 3px;
  }
  @media (max-width: 600px) {
    /* Own row on phones, so "Reset to rectangle" coming and going does not move the frame being edited. */
    .region-actions {
      flex-basis: 100%;
    }
  }
  .frame {
    border-radius: var(--radius-md);
    overflow: hidden;
  }
  .settings {
    display: grid;
    grid-template-columns: 220px minmax(0, 1fr);
    gap: var(--space-8);
    align-items: start;
  }
  .sections {
    position: sticky;
    top: calc(var(--nav-h) + var(--space-5));
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .sections a {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    padding: 9px 12px;
    border-radius: var(--radius-md);
    color: var(--c-muted);
    font-weight: 500;
  }
  .sections a strong {
    font-weight: 500;
  }
  .sections a.on,
  .sections a:hover {
    background: var(--c-surface-3);
    color: var(--c-text);
  }
  .sections a.danger {
    margin-top: var(--space-3);
    color: var(--c-danger);
  }
  .sections .summary,
  .sections :global(svg),
  .back {
    display: none;
  }
  .content {
    gap: var(--space-5);
    min-width: 0;
    max-width: 880px; /* fields as wide as a whole desktop screen are hard to read */
  }
  .content h2 {
    font-size: var(--fs-xl);
  }
  .savebar {
    position: sticky;
    bottom: 0;
    z-index: 5;
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-3) var(--space-4);
    background: var(--c-nav);
    border: 1px solid var(--c-border);
    border-radius: var(--radius-md);
  }
  @media (max-width: 800px) {
    /* A phone: the list of sections, or one section with a way back to it. */
    .settings {
      grid-template-columns: minmax(0, 1fr);
    }
    .settings.picked .sections,
    .settings:not(.picked) .content {
      display: none;
    }
    .sections {
      position: static;
      gap: 0;
      border: 1px solid var(--c-border);
      border-radius: var(--radius-lg);
      background: var(--c-surface);
      overflow: hidden;
    }
    .sections a {
      padding: var(--space-4);
      border-radius: 0;
      color: var(--c-text);
      border-top: 1px solid var(--c-border);
    }
    .sections a:first-child {
      border-top: 0;
    }
    .sections a.on {
      background: none;
    }
    .sections a strong {
      font-weight: 600;
    }
    .sections a.danger {
      margin-top: 0;
    }
    .sections .summary {
      display: block;
      font-size: var(--fs-sm);
      color: var(--c-muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .sections .col {
      gap: 3px;
      min-width: 0;
    }
    .sections :global(svg) {
      display: block;
      flex-shrink: 0;
    }
    .back {
      display: inline-flex;
      align-items: center;
      gap: var(--space-2);
      color: var(--c-muted);
    }
    .savebar {
      margin: 0 calc(-1 * var(--page-pad));
      border-radius: 0;
      border-width: 1px 0 0;
    }
  }
</style>
