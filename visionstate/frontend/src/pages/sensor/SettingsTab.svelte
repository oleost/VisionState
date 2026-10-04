<script lang="ts">
  import { untrack } from 'svelte';
  import { api } from '../../lib/api';
  import { app, toast, toastError } from '../../lib/app.svelte';
  import ConfirmButton from '../../lib/components/ConfirmButton.svelte';
  import Icon from '../../lib/components/Icon.svelte';
  import LightEditor from '../../lib/components/LightEditor.svelte';
  import LiveFrame from '../../lib/components/LiveFrame.svelte';
  import ObjectParams from '../../lib/components/ObjectParams.svelte';
  import PublishSwitch from '../../lib/components/PublishSwitch.svelte';
  import ObjectPicker from '../../lib/components/ObjectPicker.svelte';
  import DigitCells from '../../lib/components/DigitCells.svelte';
  import ReadingEditor from '../../lib/components/ReadingEditor.svelte';
  import ReadingParams from '../../lib/components/ReadingParams.svelte';
  import ReviewRulesEditor from '../../lib/components/ReviewRulesEditor.svelte';
  import SensorParams from '../../lib/components/SensorParams.svelte';
  import SourcePicker from '../../lib/components/SourcePicker.svelte';
  import StatesEditor from '../../lib/components/StatesEditor.svelte';
  import TriggersEditor from '../../lib/components/TriggersEditor.svelte';
  import { isObjectSensor } from '../../lib/objects';
  import { isReadingSensor } from '../../lib/reading';
  import { isPolygon, toRectangle } from '../../lib/roi';
  import { go, href, paths } from '../../lib/router.svelte';
  import type { ReviewRules, Roi, Sensor } from '../../lib/types';
  import { REDACTED_MARK } from '../../lib/ui';

  let { sensor, onchange }: { sensor: Sensor; onchange: () => void } = $props();

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
  let publish = $state(initial.publish);
  let review = $state(initial.review);
  const objectSensor = isObjectSensor(initial);
  let classes = $state<string[]>(initial.objects?.classes ?? []);
  let clearAfter = $state(initial.objects?.clear_after_s ?? 0);
  let minSize = $state(initial.objects?.min_size ?? 0);
  let useTaught = $state(initial.objects?.use_taught ?? true);
  const taughtCount = $derived(sensor.objects?.taught ?? 0);
  const taughtSomething = $derived(!!sensor.objects && (taughtCount > 0 || sensor.objects.custom.length > 0));
  const readingSensor = isReadingSensor(initial);
  const { value: _v, last: _l, has_image: _h, ...initialReading } = initial.reading ?? ({} as NonNullable<typeof initial.reading>);
  let reading = $state({ ...app.config!.reading_defaults, ...initialReading });
  let globalReview = $state<ReviewRules | null>(null);
  api.reviewRules().then((r) => (globalReview = r)).catch(toastError);
  let saving = $state(false);

  async function save() {
    saving = true;
    try {
      await api.updateSensor(sensor.id, {
        name,
        source_type: sourceType,
        source,
        ...(roi ? { roi } : { clear_roi: true }),
        ...(objectSensor
          ? { objects: { classes, clear_after_s: clearAfter, min_size: minSize, use_taught: useTaught } }
          : readingSensor
            ? { reading }
            : { states, review }),
        interval_s,
        threshold,
        debounce,
        triggers,
        publish,
      });
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

<div class="layout">
  <div class="col" style="gap:var(--space-5)">
    {#if source.includes(REDACTED_MARK)}
      <div class="notice warn">
        The camera password was removed when this sensor was exported. Enter the full URL again below and save.
      </div>
    {/if}
    <section class="card pad col">
      <h3>General</h3>
      <label class="field">Name <input class="input" bind:value={name} /></label>
      <SourcePicker bind:sourceType bind:source />
      <LightEditor bind:triggers lightError={sensor.live.light_error} />
      <PublishSwitch bind:publish />
    </section>

    <section class="card pad col">
      <div class="card-title">
        <h3>Region</h3>
        <span class="row region-actions" style="gap:8px">
          {#if isPolygon(roi)}
            <button class="btn sm" onclick={() => roi && (roi = toRectangle(roi))}>Reset to rectangle</button>
          {/if}
          <button class="btn sm" onclick={() => (roi = null)}><Icon name="frame" size={14} /> Use full frame</button>
        </span>
      </div>
      <p class="small muted">
        Drag to draw a new box, move it, or shape it with corners.
        {objectSensor
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
          showLive={false}
          interval={10_000}
          light={triggers.light_entity ? { entity: triggers.light_entity, delay: triggers.light_delay_s } : null}
        >
          {#if readingSensor && reading.display === 'counter'}<DigitCells {roi} digits={reading.digits} />{/if}
        </LiveFrame>
      </div>
    </section>
  </div>

  <div class="col" style="gap:var(--space-5)">
    {#if readingSensor}
      <section class="card pad col">
        <h3>Reading</h3>
        <ReadingEditor bind:value={reading} seen={sensor.reading?.last?.text} />
      </section>
    {:else if objectSensor}
      <section class="card pad col">
        <h3>Objects</h3>
        <p class="small muted">Each object gets an on/off sensor and a count in Home Assistant.</p>
        <ObjectPicker bind:selected={classes} />
      </section>
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
                  (<a href={href(paths.sensor(sensor.id, 'quality'))}>see them</a>).
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
            <span class="xsmall faint">Every taught box and own label, with its entities.</span>
          </div>
        </section>
      {/if}
    {:else}
      <section class="card pad col">
        <h3>States</h3>
        <p class="small muted">Removing a state also removes its labels. Renaming keeps the Home Assistant value.</p>
        <StatesEditor bind:states />
      </section>
    {/if}

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
          bind:spotRate={reading.spot_rate}
          mode={reading.mode}
          unit={reading.unit}
        />
      {:else}
        <SensorParams bind:threshold bind:debounce />
      {/if}
    </section>

    {#if !objectSensor && !readingSensor}
      <section class="card pad col">
        <h3>Review</h3>
        {#if globalReview}
          <ReviewRulesEditor bind:value={review} inherited={globalReview} {threshold} />
        {/if}
      </section>
    {/if}

    <div class="row">
      <button class="btn primary" disabled={saving || (objectSensor && !classes.length)} onclick={save}>{saving ? 'Saving…' : 'Save changes'}</button>
      <span class="spacer"></span>
      <ConfirmButton onconfirm={remove} confirmLabel="Click again to delete everything">
        <Icon name="trash" size={14} /> Delete sensor
      </ConfirmButton>
    </div>
  </div>
</div>

<style>
  .taught {
    align-items: flex-start;
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
  .layout {
    display: grid;
    grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr);
    gap: var(--space-5);
    align-items: start;
  }
  .frame {
    border-radius: var(--radius-md);
    overflow: hidden;
  }
  @media (max-width: 1000px) {
    .layout {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
