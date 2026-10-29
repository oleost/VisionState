<script lang="ts">
  // A sound sensor's settings: its name and stream, what it listens for, clips, review and output.
  import { untrack } from 'svelte';
  import { api } from '../../lib/api';
  import { app, toast, toastError } from '../../lib/app.svelte';
  import ConfirmButton from '../../lib/components/ConfirmButton.svelte';
  import Icon from '../../lib/components/Icon.svelte';
  import PublishSwitch from '../../lib/components/PublishSwitch.svelte';
  import SoundEditor from '../../lib/components/sound/SoundEditor.svelte';
  import StreamField from '../../lib/components/sound/StreamField.svelte';
  import { go, paths } from '../../lib/router.svelte';
  import type { ReviewRules, Sensor, SoundSettings } from '../../lib/types';
  import { REDACTED_MARK } from '../../lib/ui';

  let { sensor, onchange }: { sensor: Sensor; onchange: () => void } = $props();

  const initial = untrack(() => $state.snapshot(sensor));
  let name = $state(initial.name);
  let source = $state(initial.source);
  let publish = $state(initial.publish);
  let review = $state(initial.review);
  const { custom: _c, live: _l, levels: _v, line: _n, analysed: _a, stream: _s, ...settings } = initial.sound!;
  let sound = $state<SoundSettings>({ ...app.config!.sound_defaults, ...settings });
  const before = { ...settings.thresholds };
  let globalReview = $state<ReviewRules | null>(null);
  api.reviewRules().then((r) => (globalReview = r)).catch(toastError);

  function questionsEvery(seconds: number): string {
    const minutes = Math.round(seconds / 60);
    if (seconds <= 0) return 'Every sound only just heard is asked about';
    return minutes <= 1 ? 'At most one question a minute' : `At most one question per ${minutes} minutes`;
  }
  let saving = $state(false);
  const hasVideo = $derived(sensor.sound?.stream ? !!sensor.sound.stream.video : null);

  async function save() {
    saving = true;
    try {
      // A threshold set back to the model's is sent as null (removed).
      const thresholds: Record<string, number | null> = { ...sound.thresholds };
      for (const key of Object.keys(before)) if (!(key in thresholds)) thresholds[key] = null;
      await api.updateSensor(sensor.id, { name, source, publish, review, sound: { ...sound, thresholds } });
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
    {#if source.includes(REDACTED_MARK)}
      <div class="notice warn">The camera password was removed when this sensor was exported. Enter the full URL again below and save.</div>
    {/if}
    <section class="card pad col">
      <h3>General</h3>
      <label class="field">Name <input class="input" bind:value={name} /></label>
      <StreamField bind:source />
      <PublishSwitch bind:publish />
    </section>
    <section class="card pad col">
      <h3>Review</h3>
      <p class="small muted">With clips on, a sound that was only just heard is asked about in the review queue: listen, and answer.</p>
      {#if globalReview}
        <label class="field">
          Ask in the review queue
          <select
            class="input"
            value={review.enabled === null ? 'inherit' : review.enabled ? 'on' : 'off'}
            onchange={(e) => (review.enabled = e.currentTarget.value === 'inherit' ? null : e.currentTarget.value === 'on')}
          >
            <option value="inherit">Like all sensors ({globalReview.enabled ? 'on' : 'off'})</option>
            <option value="on">On</option>
            <option value="off">Off</option>
          </select>
        </label>
        <p class="xsmall faint">{questionsEvery(review.cooldown_s ?? globalReview.cooldown_s)} (Settings → Review).</p>
      {/if}
    </section>
  </div>

  <div class="col" style="gap:var(--space-5)">
    <section class="card pad col">
      <h3>Listening</h3>
      <SoundEditor bind:value={sound} own={sensor.sound?.custom ?? []} {hasVideo} />
    </section>
    <div class="row">
      <button class="btn primary" disabled={saving || !source.trim()} onclick={save}>{saving ? 'Saving…' : 'Save changes'}</button>
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
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: var(--space-5);
    align-items: start;
  }
  @media (max-width: 1000px) {
    .layout {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
