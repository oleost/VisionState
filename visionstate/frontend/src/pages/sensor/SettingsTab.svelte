<script lang="ts">
  import { untrack } from 'svelte';
  import { api } from '../../lib/api';
  import { toast, toastError } from '../../lib/app.svelte';
  import ConfirmButton from '../../lib/components/ConfirmButton.svelte';
  import Icon from '../../lib/components/Icon.svelte';
  import LiveFrame from '../../lib/components/LiveFrame.svelte';
  import SensorParams from '../../lib/components/SensorParams.svelte';
  import SourcePicker from '../../lib/components/SourcePicker.svelte';
  import StatesEditor from '../../lib/components/StatesEditor.svelte';
  import TriggersEditor from '../../lib/components/TriggersEditor.svelte';
  import { go, paths } from '../../lib/router.svelte';
  import type { Roi, Sensor } from '../../lib/types';

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
  let saving = $state(false);

  async function save() {
    saving = true;
    try {
      await api.updateSensor(sensor.id, {
        name,
        source_type: sourceType,
        source,
        ...(roi ? { roi } : { clear_roi: true }),
        states,
        interval_s,
        threshold,
        debounce,
        triggers,
      });
      toast('Settings saved');
      onchange();
    } catch (err) {
      toastError(err);
    } finally {
      saving = false;
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
    <section class="card pad col">
      <h3>General</h3>
      <label class="field">Name <input class="input" bind:value={name} /></label>
      <SourcePicker bind:sourceType bind:source />
    </section>

    <section class="card pad col">
      <div class="card-title">
        <h3>Region</h3>
        <button class="btn sm" onclick={() => (roi = null)}><Icon name="frame" size={14} /> Use full frame</button>
      </div>
      <p class="small muted">Drag to draw a new box, or move and resize the existing one. Changing it retrains the model.</p>
      <div class="frame"><LiveFrame sensorId={sensor.id} bind:roi editable showLive={false} interval={10_000} /></div>
    </section>
  </div>

  <div class="col" style="gap:var(--space-5)">
    <section class="card pad col">
      <h3>States</h3>
      <p class="small muted">Removing a state also removes its labels. Renaming keeps the Home Assistant value.</p>
      <StatesEditor bind:states />
    </section>

    <section class="card pad col">
      <h3>When to check</h3>
      <TriggersEditor bind:triggers bind:interval_s liveScore={sensor.live.change_score} />
    </section>

    <section class="card pad col">
      <h3>Sensor output</h3>
      <SensorParams bind:threshold bind:debounce />
    </section>

    <div class="row">
      <button class="btn primary" disabled={saving} onclick={save}>{saving ? 'Saving…' : 'Save changes'}</button>
      <span class="spacer"></span>
      <ConfirmButton onconfirm={remove} confirmLabel="Click again to delete everything">
        <Icon name="trash" size={14} /> Delete sensor
      </ConfirmButton>
    </div>
  </div>
</div>

<style>
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
