<script lang="ts">
  // A sound sensor's clip: a video with its sound when the camera had video, else the sound with a
  // player sized for a thumb. Nothing loads or plays until it is tapped (phones play sound only then).
  import Icon from '../Icon.svelte';

  let { src, video = false, label = 'Play the clip' }: { src: string; video?: boolean; label?: string } = $props();

  let started = $state(false);
</script>

{#if video}
  {#if started}
    <!-- svelte-ignore a11y_media_has_caption -->
    <video class="clip" {src} controls autoplay playsinline preload="auto"></video>
  {:else}
    <button type="button" class="poster" onclick={() => (started = true)} aria-label={label}>
      <span class="play"><Icon name="play" size={22} /></span>
      <span class="small">Watch with sound</span>
    </button>
  {/if}
{:else if started}
  <audio class="sound" {src} controls autoplay preload="auto"></audio>
{:else}
  <button type="button" class="btn listen" onclick={() => (started = true)} aria-label={label}>
    <Icon name="sound" size={16} /> Listen
  </button>
{/if}

<style>
  .clip {
    display: block;
    width: 100%;
    max-height: 60vh;
    border-radius: var(--radius-md);
    background: #000;
  }
  .poster {
    width: 100%;
    aspect-ratio: 16 / 9;
    max-height: 40vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--space-2);
    border: 1px solid var(--c-border);
    border-radius: var(--radius-md);
    background: var(--c-sunken);
    color: var(--c-text);
    font: inherit;
    cursor: pointer;
  }
  .play {
    width: 52px;
    height: 52px;
    border-radius: var(--radius-pill);
    background: var(--c-accent);
    color: var(--c-accent-ink);
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .sound {
    width: 100%;
    min-height: var(--touch);
  }
  .listen {
    min-height: var(--touch);
  }
</style>
