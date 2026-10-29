<script lang="ts">
  // Sound sensors: what the stream sounds like now (its level and the line a window must reach to
  // be analysed), each sound's score against its threshold, and the stream itself.
  import { app } from '../../lib/app.svelte';
  import EntitiesCard from '../../lib/components/EntitiesCard.svelte';
  import ProbBars from '../../lib/components/ProbBars.svelte';
  import Icon from '../../lib/components/Icon.svelte';
  import LevelChart from '../../lib/components/sound/LevelChart.svelte';
  import { pct } from '../../lib/format';
  import { href, paths } from '../../lib/router.svelte';
  import { soundColor } from '../../lib/sound';
  import type { Sensor } from '../../lib/types';

  let { sensor }: { sensor: Sensor; onchange?: () => void } = $props();

  const sound = $derived(sensor.sound!);
  const stream = $derived(sound.stream);
</script>

<div class="layout">
  <div class="col main">
    <section class="card pad col">
      <div class="card-title">
        <h3>Listening</h3>
        <span class="spacer"></span>
        {#if sensor.live.available === false}
          <span class="chip danger">No sound</span>
        {:else if sound.analysed}
          <span class="chip ok">Analysing</span>
        {:else}
          <span class="chip muted">Quiet</span>
        {/if}
      </div>
      {#if sensor.live.error}
        <div class="notice danger small" data-testid="sound-error"><Icon name="alert" /><span>{sensor.live.error}</span></div>
      {/if}
      <LevelChart levels={sound.levels} line={sound.line} floor={Math.min(app.config?.sound.floor_db ?? -70, -70) - 20} />
      <p class="xsmall muted">
        Only sound above the line is analysed: {sound.level === 'auto'
          ? `${sound.above_db} dB above the background it learns`
          : `a fixed ${sound.fixed_db} dB`}. A sound that matters but stays below it — a beep two rooms away — is
        missed: lower the line under <a href={href(paths.sensor(sensor.id, 'settings'))}>Settings</a>.
      </p>
    </section>

    <section class="card pad col">
      <div class="card-title"><h3>Sounds</h3><span class="xsmall faint">the last loud window, against each sound's threshold</span></div>
      {#if sound.live.length}
        <ProbBars
          bars={sound.live.map((s) => ({ key: s.key, name: s.name, color: soundColor(sensor, s.key), value: s.score, threshold: s.threshold }))}
        >
          {#snippet tags(key)}
            {@const s = sound.live.find((x) => x.key === key)}
            {#if s?.own}<span class="chip muted">own</span>{/if}
            {#if s?.on}<span class="chip ok">Heard</span>{/if}
          {/snippet}
        </ProbBars>
      {:else}
        <p class="small muted">It listens for no sound. Pick some under <a href={href(paths.sensor(sensor.id, 'settings'))}>Settings</a>.</p>
      {/if}
    </section>
  </div>

  <aside class="col">
    <section class="card pad col stream">
      <div class="card-title"><h3>The stream</h3></div>
      {#if stream}
        <span class="small"><Icon name="sound" size={14} /> {stream.codec}, {stream.sample_rate / 1000} kHz{stream.channels > 1 ? `, ${stream.channels} channels` : ''}</span>
        {#if stream.sample_rate <= 8000}
          <span class="xsmall warn-text">Telephone quality: nothing above 4 kHz is heard. Crying and breaking glass suffer; alarms, sirens and barks do not.</span>
        {/if}
        {#if stream.video}
          <span class="small"><Icon name="camera" size={14} /> {stream.video.codec}, {stream.video.width}×{stream.video.height}</span>
          {#if stream.video.codec === 'hevc'}
            <span class="xsmall warn-text">H.265 video does not play in every browser: for clips with video, use the camera's H.264 (sub) stream.</span>
          {/if}
        {:else}
          <span class="xsmall muted">Sound only (no video).</span>
        {/if}
      {:else}
        <span class="small muted">Connecting…</span>
      {/if}
      <span class="xsmall faint">Clips: {sound.clips ? (sound.video && stream?.video ? 'with video' : 'sound only') : 'off'}</span>
    </section>
    <EntitiesCard {sensor} />
  </aside>
</div>

<style>
  .layout {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 340px;
    gap: var(--space-6);
    align-items: start;
  }
  .main,
  aside {
    gap: var(--space-4);
  }
  .stream {
    gap: var(--space-2);
  }
  .stream span {
    overflow-wrap: anywhere;
  }
  .warn-text {
    color: var(--c-warn-text);
  }
  @media (max-width: 1000px) {
    .layout {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
