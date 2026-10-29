<script lang="ts">
  // New sound sensor: listen to the stream for a few seconds (does it have sound, how loud, which of
  // the model's sounds it hears), pick what to listen for, and see its entities in Home Assistant.
  import { untrack } from 'svelte';
  import { api } from '../../lib/api';
  import { app } from '../../lib/app.svelte';
  import LevelChart from '../../lib/components/sound/LevelChart.svelte';
  import SoundEditor from '../../lib/components/sound/SoundEditor.svelte';
  import { haSlug, pct } from '../../lib/format';
  import type { SoundPreview, SoundSettings } from '../../lib/types';
  import HaPreview from './HaPreview.svelte';
  import TestCard from './TestCard.svelte';

  let { source, name: sensorName, sound = $bindable() }: { source: string; name: string; sound: SoundSettings } = $props();

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
  const hasVideo = $derived(preview?.stream ? !!preview.stream.video : null);
</script>

<div class="col" style="gap:var(--space-3)">
  <h3>Which sounds?</h3>
  <SoundEditor bind:value={sound} {hasVideo} />
</div>

<div data-testid="sound-test">
  <TestCard busy={listening} onretest={listen}>
    {#snippet status()}
      {#if listening && !preview}
        Listening for a few seconds…
      {:else if error}
        <span class="danger-text">{error}</span>
      {:else if preview && !preview.ok}
        <span class="danger-text" data-testid="sound-test-error"
          >{preview.error}. Check the address, and that the camera's microphone (its audio) is switched on.</span
        >
      {:else if preview}
        {preview.stream?.codec}, {(preview.stream?.sample_rate ?? 0) / 1000} kHz
        {#if preview.stream?.video}· video {preview.stream.video.width}×{preview.stream.video.height} (clips can show it){/if}
      {/if}
    {/snippet}
    {#if preview?.ok}
      <div class="col heard">
        {#if preview.levels?.length}<LevelChart levels={preview.levels} line={null} />{/if}
        {#if preview.model_error}
          <span class="xsmall warn-text">{preview.model_error}</span>
        {:else if preview.sounds?.length}
          <span class="xsmall muted">
            Loudest moment:
            {preview.sounds.slice(0, 3).map((s) => `${name(s.key)} ${pct(s.score)}`).join(' · ')}
          </span>
        {/if}
      </div>
    {/if}
  </TestCard>
</div>

<HaPreview>
  {#each sound.sounds as key (key)}
    <span class="mono small">binary_sensor.{haSlug(`${sensorName} ${name(key)}`)}</span>
  {:else}
    <span class="xsmall muted">Pick at least one sound.</span>
  {/each}
  <span class="xsmall muted">Own sounds join them once taught.</span>
</HaPreview>

<style>
  .heard {
    gap: var(--space-2);
    padding: 0 16px 16px;
  }
  .warn-text {
    color: var(--c-warn-text);
  }
  .danger-text {
    color: var(--c-danger-text);
  }
</style>
