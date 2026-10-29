<script lang="ts">
  // New sound sensor: listen to the stream for a few seconds (does it have sound, how loud, which of
  // the model's sounds it hears), then pick what to listen for.
  import { untrack } from 'svelte';
  import { api } from '../../lib/api';
  import { app } from '../../lib/app.svelte';
  import Icon from '../../lib/components/Icon.svelte';
  import LevelChart from '../../lib/components/sound/LevelChart.svelte';
  import SoundEditor from '../../lib/components/sound/SoundEditor.svelte';
  import { pct } from '../../lib/format';
  import type { SoundPreview, SoundSettings } from '../../lib/types';

  let { source, sound = $bindable() }: { source: string; sound: SoundSettings } = $props();

  let preview = $state<SoundPreview | null>(null);
  let listening = $state(false);
  let error = $state('');

  export async function listen() {
    listening = true;
    error = '';
    try {
      preview = await api.soundPreview(source);
    } catch (err) {
      error = (err as Error).message;
    } finally {
      listening = false;
    }
  }

  $effect(() => {
    void source;
    untrack(() => listen());
  });

  const name = (key: string) => app.config?.sounds.find((s) => s.key === key)?.name ?? key;
</script>

<div class="col" style="gap:6px">
  <h2>What should it listen for?</h2>
  <p class="muted">Pick the sounds that matter here. Sounds of your own — your doorbell, the washing machine — are taught once the sensor runs.</p>
</div>

<section class="card pad col test" data-testid="sound-test">
  <div class="row wrap">
    <strong class="small">The stream</strong>
    <span class="spacer"></span>
    <button class="btn sm" disabled={listening} onclick={listen}><Icon name="refresh" size={14} /> {listening ? 'Listening…' : 'Listen again'}</button>
  </div>
  {#if listening && !preview}
    <p class="small muted">Listening for a few seconds…</p>
  {:else if error}
    <div class="notice danger small"><Icon name="alert" /><span>{error}</span></div>
  {:else if preview && !preview.ok}
    <div class="notice danger small" data-testid="sound-test-error">
      <Icon name="alert" /><span>{preview.error}. Check the address, and that the camera's microphone (its audio) is switched on.</span>
    </div>
  {:else if preview}
    <span class="small">
      <Icon name="sound" size={14} /> {preview.stream?.codec}, {(preview.stream?.sample_rate ?? 0) / 1000} kHz
      {#if preview.stream?.video} · <Icon name="camera" size={14} /> video {preview.stream.video.width}×{preview.stream.video.height} (clips can show it){/if}
    </span>
    {#if preview.levels?.length}<LevelChart levels={preview.levels} line={null} />{/if}
    {#if preview.model_error}
      <span class="xsmall warn-text">{preview.model_error}</span>
    {:else if preview.sounds?.length}
      <span class="xsmall muted">
        Loudest moment:
        {preview.sounds.slice(0, 3).map((s) => `${name(s.key)} ${pct(s.score)}`).join(' · ')}
      </span>
    {/if}
  {/if}
</section>

<SoundEditor bind:value={sound} hasVideo={preview?.stream ? !!preview.stream.video : null} />

<style>
  .test {
    gap: var(--space-3);
    max-width: 760px;
  }
  .warn-text {
    color: var(--c-warn-text);
  }
</style>
