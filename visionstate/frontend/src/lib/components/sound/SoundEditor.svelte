<script lang="ts">
  // What a sound sensor listens for and when: the model's sounds (each with its threshold), the
  // level a window must reach to be analysed, how long a sound stays heard, clips and learning the
  // room. Defaults and limits come from the backend (config).
  import { app } from '../../app.svelte';
  import { pct } from '../../format';
  import type { OwnSound, SoundSettings } from '../../types';

  let {
    value = $bindable(),
    own = [],
    hasVideo = null,
  }: { value: SoundSettings; own?: OwnSound[]; hasVideo?: boolean | null } = $props();

  const config = $derived(app.config!);
  const limits = $derived(config.sound_limits);
  const chosen = $derived([
    ...config.sounds.filter((s) => value.sounds.includes(s.key)).map((s) => ({ key: s.key, name: s.name, model: s.threshold })),
    ...own.map((c) => ({ key: c.key, name: c.name, model: config.sound.own_threshold })),
  ]);

  function toggle(key: string, on: boolean) {
    value.sounds = on ? [...value.sounds, key] : value.sounds.filter((k) => k !== key);
  }

  function setThreshold(key: string, percent: number, model: number) {
    const next = { ...value.thresholds };
    const v = Math.round(percent) / 100;
    if (Math.abs(v - model) < 0.005) delete next[key];
    else next[key] = v;
    value.thresholds = next;
  }
</script>

<div class="col editor">
  <fieldset class="col">
    <legend class="field">Sounds to listen for</legend>
    <div class="sounds">
      {#each config.sounds as s (s.key)}
        <label class="check pick" class:on={value.sounds.includes(s.key)}>
          <input type="checkbox" checked={value.sounds.includes(s.key)} onchange={(e) => toggle(s.key, e.currentTarget.checked)} />
          <span>{s.name}</span>
        </label>
      {/each}
    </div>
    <p class="xsmall muted">
      Each is an on/off sensor in Home Assistant (a smoke alarm as a smoke sensor — on top of your smoke detectors, never
      instead of them). For a sound of your own, such as your doorbell, teach it under Own sounds.
    </p>
  </fieldset>

  {#if chosen.length}
    <fieldset class="col">
      <legend class="field">Heard from</legend>
      {#each chosen as s (s.key)}
        {@const current = value.thresholds[s.key] ?? s.model}
        <label class="col threshold">
          <span class="row small"><span>{s.name}</span><span class="spacer"></span><span class="mono muted">{pct(current)}</span></span>
          <input
            type="range"
            min={Math.round(limits.threshold[0] * 100)}
            max={Math.round(limits.threshold[1] * 100)}
            value={Math.round(current * 100)}
            oninput={(e) => setThreshold(s.key, Number(e.currentTarget.value), s.model)}
            aria-label="{s.name}: heard from"
          />
        </label>
      {/each}
      <p class="xsmall muted">
        A sound is heard when its score passes this in two of the last three windows. Lower it if a sound is missed, raise it if
        it is heard when it was something else. The Live tab shows each score.
      </p>
    </fieldset>
  {/if}

  <fieldset class="col">
    <legend class="field">Analyse</legend>
    <label class="check">
      <input type="radio" name="level" checked={value.level === 'auto'} onchange={() => (value.level = 'auto')} />
      <span class="col" style="gap:2px">
        <span class="small">Sound above the background</span>
        <span class="xsmall faint">It learns how quiet the room is, and analyses what is louder by:</span>
      </span>
    </label>
    {#if value.level === 'auto'}
      <label class="field inline">
        <input class="input sm" type="number" min={limits.above_db[0]} max={limits.above_db[1]} step="1" bind:value={value.above_db} />
        dB above the background
      </label>
    {/if}
    <label class="check">
      <input type="radio" name="level" checked={value.level === 'fixed'} onchange={() => (value.level = 'fixed')} />
      <span class="small">Sound above a fixed level</span>
    </label>
    {#if value.level === 'fixed'}
      <label class="field inline">
        <input class="input sm" type="number" min={limits.fixed_db[0]} max={limits.fixed_db[1]} step="1" bind:value={value.fixed_db} />
        dB (0 is the loudest the stream can be)
      </label>
    {/if}
  </fieldset>

  <label class="field inline">
    A sound stays heard for
    <input class="input sm" type="number" min={limits.clear_after_s[0]} max={limits.clear_after_s[1]} step="1" bind:value={value.clear_after_s} />
    s after it was last heard
  </label>

  <fieldset class="col">
    <legend class="field">Clips</legend>
    <label class="check">
      <input type="checkbox" bind:checked={value.clips} aria-label="Keep clips" />
      <span class="col" style="gap:2px">
        <strong class="small">Keep a clip of each sound heard</strong>
        <span class="xsmall faint">
          A few seconds before and after, in the history, to listen to and to teach own sounds from. Speech in a clip is
          silenced before it is kept, and “Someone talking” never keeps one. Kept in the app's own storage, as long as the
          history.
        </span>
      </span>
    </label>
    {#if value.clips && hasVideo !== false}
      <label class="check">
        <input type="checkbox" bind:checked={value.video} aria-label="Clips with video" />
        <span class="col" style="gap:2px">
          <strong class="small">With the camera's video</strong>
          <span class="xsmall faint">
            {hasVideo === null ? 'When the stream has video: ' : ''}see what happened, with its sound. Off: sound only, much
            smaller.
          </span>
        </span>
      </label>
    {/if}
  </fieldset>

  {#if own.length}
    <label class="check">
      <input type="checkbox" bind:checked={value.learn_room} aria-label="Learn the room's sounds" />
      <span class="col" style="gap:2px">
        <strong class="small">Learn the room's sounds</strong>
        <span class="xsmall faint">
          Loud sounds that are none of your own sounds are kept as “other”, so your own sounds are told apart from what this
          room sounds like. Off: only what you answered “no” for in Review.
        </span>
      </span>
    </label>
  {/if}
</div>

<style>
  .editor {
    gap: var(--space-5);
  }
  fieldset {
    border: none;
    padding: 0;
    margin: 0;
    gap: var(--space-2);
  }
  .sounds {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: var(--space-2);
  }
  .pick {
    align-items: center;
    padding: 10px 12px;
    min-height: var(--touch);
    border: 1px solid var(--c-border-strong);
    border-radius: var(--radius-md);
  }
  .pick.on {
    border-color: var(--c-accent-line);
    background: var(--c-accent-soft);
  }
  .threshold {
    gap: 4px;
  }
  .threshold input {
    width: 100%;
  }
  .inline {
    flex-direction: row;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-2);
    font-weight: 400;
  }
  .inline input {
    width: 90px;
  }
</style>
