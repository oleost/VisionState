<script lang="ts">
  // Recording an own sound from the sensor's own stream: it starts as it appears (the Record tap
  // was Start), Stop ends it, and it goes on for some seconds more, as the stream comes late. Then
  // the clip is trimmed (ClipTrimmer): the part from Start to Stop is kept at first, and the seconds
  // after are there to take in. Teach keeps the part as a clip of the own sound (`label`, or a new
  // one: `newName`); a recording not taught is discarded when this goes away.
  import { onDestroy, untrack } from 'svelte';
  import { api } from '../../api';
  import { app, toast, toastError } from '../../app.svelte';
  import Icon from '../Icon.svelte';
  import ClipTrimmer from './ClipTrimmer.svelte';
  import type { SoundRecording } from '../../types';

  let {
    sensorId,
    label,
    newName,
    target,
    ondone,
    onclose,
  }: {
    sensorId: number;
    label: string | null;
    newName?: string;
    /** What it is taught as, for the button ("Doorbell", "other sounds"). */
    target: string;
    ondone: () => void;
    onclose: () => void;
  } = $props();

  type Phase = 'starting' | 'recording' | 'finishing' | 'trim' | 'teaching';
  let phase = $state<Phase>('starting');
  let id: string | null = null;
  let maxS = $state(app.config?.sound.record_max_s ?? 20);
  let afterS = $state(app.config?.sound.record_after_s ?? 8);
  const minS = $derived(app.config?.sound.record_min_s ?? 0.5);
  let elapsed = $state(0);
  let left = $state(0); // seconds still to wait after Stop
  let recording = $state<SoundRecording | null>(null);
  let start = $state(0);
  let end = $state(0);
  let timer: ReturnType<typeof setInterval> | null = null;
  let taught = false;
  let gone = false;

  untrack(() => begin());

  async function begin() {
    phase = 'starting';
    recording = null;
    elapsed = 0;
    try {
      const made = await api.startSoundRecording(sensorId);
      if (gone) return void api.discardSoundRecording(sensorId, made.id).catch(() => {});
      id = made.id;
      maxS = made.max_s;
      afterS = made.after_s;
      phase = 'recording';
      const t0 = performance.now();
      tick(() => {
        elapsed = (performance.now() - t0) / 1000;
        if (elapsed >= maxS) stop();
      });
    } catch (err) {
      toastError(err);
      onclose();
    }
  }

  function tick(fn: () => void) {
    clear();
    timer = setInterval(fn, 100);
  }

  function clear() {
    if (timer) clearInterval(timer);
    timer = null;
  }

  async function stop() {
    if (phase !== 'recording' || !id) return;
    phase = 'finishing';
    const t0 = performance.now();
    left = afterS;
    tick(() => (left = Math.max(0, afterS - (performance.now() - t0) / 1000)));
    try {
      const made = await api.stopSoundRecording(sensorId, id);
      if (gone) return;
      recording = made;
      start = 0;
      end = Math.min(made.seconds, Math.max(made.stop_s, minS));
      phase = 'trim';
    } catch (err) {
      toastError(err);
      onclose();
    } finally {
      clear();
    }
  }

  async function teach() {
    if (!id || !recording) return;
    phase = 'teaching';
    try {
      await api.teachSoundRecording(sensorId, id, start, end, label, newName);
      taught = true;
      toast('Clip taught');
      ondone();
    } catch (err) {
      toastError(err);
      phase = 'trim';
    }
  }

  function discard() {
    if (id && !taught) void api.discardSoundRecording(sensorId, id).catch(() => {});
    id = null;
  }

  function again() {
    discard();
    begin();
  }

  function cancel() {
    discard();
    onclose();
  }

  onDestroy(() => {
    gone = true;
    clear();
    discard();
  });

  const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
</script>

<div class="col recorder" data-testid="sound-recorder">
  {#if phase === 'starting' || phase === 'recording'}
    <div class="row wrap live">
      <span class="dot" class:on={phase === 'recording'} aria-hidden="true"></span>
      <span class="mono" data-testid="record-clock">{clock(elapsed)}</span>
      <span class="small muted">{phase === 'starting' ? 'Starting…' : `Recording from the sensor's stream · at most ${clock(maxS)}`}</span>
    </div>
    <div class="bar" aria-hidden="true"><div style:width="{Math.min(100, (elapsed / maxS) * 100)}%"></div></div>
    <p class="small muted">Make the sound now, then tap Stop.</p>
    <div class="row wrap buttons">
      <button type="button" class="btn primary" onclick={stop} disabled={phase !== 'recording'} data-testid="record-stop">
        <Icon name="stop" size={14} /> Stop
      </button>
      <button type="button" class="btn ghost" onclick={cancel}>Cancel</button>
    </div>
  {:else if phase === 'finishing'}
    <div class="row wrap live">
      <span class="dot on" aria-hidden="true"></span>
      <span class="small">Listening {Math.ceil(left)} s more: the stream comes a little late</span>
    </div>
    <div class="bar" aria-hidden="true"><div style:width="{100 - (left / afterS) * 100}%"></div></div>
  {:else if recording}
    <p class="small muted">
      Drag the ends so that only the sound is kept, and play it to check. The seconds after <em>Stop</em> are there because the
      stream comes a little late: move the end into them when the sound is cut short.
    </p>
    <ClipTrimmer
      src={api.soundRecordingUrl(sensorId, recording.id)}
      peaks={recording.peaks}
      perS={recording.peaks_per_s}
      seconds={recording.seconds}
      mark={recording.stop_s}
      min={minS}
      bind:start
      bind:end
    />
    {#if recording.muted_s}
      <p class="xsmall faint">{recording.muted_s} s with someone talking were silenced, as in every clip.</p>
    {/if}
    <div class="row wrap buttons">
      <button type="button" class="btn primary" onclick={teach} disabled={phase === 'teaching'} data-testid="record-teach">
        <Icon name="check" size={14} /> {phase === 'teaching' ? 'Teaching…' : `Teach as ${target}`}
      </button>
      <button type="button" class="btn" onclick={again} disabled={phase === 'teaching'}><Icon name="record" size={14} /> Record again</button>
      <button type="button" class="btn ghost" onclick={cancel} disabled={phase === 'teaching'}>Discard</button>
    </div>
  {/if}
</div>

<style>
  .recorder {
    gap: var(--space-3);
    padding: var(--space-3);
    border: 1px solid var(--c-border);
    border-radius: var(--radius-md);
    background: var(--c-surface-2);
  }
  .live {
    gap: var(--space-2);
    align-items: center;
  }
  .dot {
    width: 12px;
    height: 12px;
    border-radius: var(--radius-pill);
    background: var(--c-border-strong);
    flex: none;
  }
  .dot.on {
    background: var(--c-danger);
    animation: pulse 1.2s ease-in-out infinite;
  }
  @keyframes pulse {
    50% {
      opacity: 0.35;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .dot.on {
      animation: none;
    }
  }
  .bar {
    height: 4px;
    border-radius: var(--radius-pill);
    background: var(--c-sunken);
    overflow: hidden;
  }
  .bar div {
    height: 100%;
    background: var(--c-danger);
    transition: width 0.1s linear;
  }
  .buttons {
    gap: var(--space-2);
  }
</style>
