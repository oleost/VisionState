<script lang="ts">
  // Periodically fetches a frame for a sensor. Reports the frame id so labels hit the frame on screen.
  // With `light` (the sensor's light), it holds the light on while open and fetches a fresh frame
  // as soon as it is bright; until then the backend shows the frame of the last check.
  import type { Snippet } from 'svelte';
  import { onDestroy } from 'svelte';
  import { api } from '../api';
  import { ago } from '../format';
  import type { Roi } from '../types';
  import { POLL } from '../ui';
  import LightHold from './LightHold.svelte';
  import RoiEditor from './RoiEditor.svelte';

  let {
    sensorId,
    roi = $bindable(null),
    editable = false,
    cached = false,
    interval = POLL.liveFrame,
    frozen = false,
    showLive = true,
    frameId = $bindable(null),
    light = null,
    children,
  }: {
    sensorId: number;
    roi?: Roi | null;
    editable?: boolean;
    cached?: boolean;
    interval?: number;
    frozen?: boolean;
    showLive?: boolean;
    frameId?: string | null;
    light?: { entity: string; delay: number } | null;
    children?: Snippet;
  } = $props();

  let url: string | null = $state(null);
  let error = $state('');
  let updated = $state<number | null>(null);
  let now = $state(Date.now());
  let timer: ReturnType<typeof setTimeout> | undefined;
  let alive = true;
  let lightReady = $state(false);

  async function tick() {
    if (!alive) return;
    // Always load the first frame; after that, only refresh while the page is visible.
    if (!frozen && (updated === null || document.visibilityState === 'visible')) {
      try {
        const frame = await api.frame(sensorId, cached);
        if (!alive) return URL.revokeObjectURL(frame.url);
        if (url) URL.revokeObjectURL(url);
        url = frame.url;
        frameId = frame.frameId;
        updated = Date.now();
        error = '';
      } catch (err) {
        error = (err as Error).message;
      }
    }
    now = Date.now();
    timer = setTimeout(tick, interval);
  }

  $effect(() => {
    void sensorId;
    void lightReady; // the light is bright now: take a fresh frame right away
    clearTimeout(timer);
    tick();
  });

  function onVisible() {
    if (document.visibilityState === 'visible') {
      clearTimeout(timer);
      tick();
    }
  }

  onDestroy(() => {
    alive = false;
    clearTimeout(timer);
    if (url) URL.revokeObjectURL(url);
  });
</script>

<svelte:document onvisibilitychange={onVisible} />

{#if light?.entity}
  <div class="light-slot"><LightHold entity={light.entity} delay={light.delay} bind:ready={lightReady} /></div>
{/if}
<div class="live">
  <RoiEditor src={url} bind:roi {editable}>
    {#if showLive && updated}
      <span class="badge" class:frozen>
        <span class="dot" style:background={frozen ? 'var(--c-info)' : 'var(--c-danger)'}></span>
        {frozen ? 'FROZEN' : 'LIVE'}
        <span class="when">{ago(updated / 1000, now)}</span>
      </span>
    {/if}
    {#if error}
      <div class="error"><span>{error}</span></div>
    {/if}
    {@render children?.()}
  </RoiEditor>
</div>

<style>
  .light-slot {
    margin-bottom: var(--space-2);
  }
  .live {
    position: relative;
  }
  .badge {
    position: absolute;
    right: 12px;
    top: 12px;
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    height: 26px;
    padding: 0 10px;
    border-radius: var(--radius-pill);
    background: var(--c-overlay);
    font-size: var(--fs-sm);
    font-weight: 600;
    pointer-events: none;
  }
  .when {
    font-weight: 400;
    color: var(--c-text-2);
  }
  .error {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--space-6);
    text-align: center;
    color: var(--c-danger-text);
    background: rgba(15, 18, 22, 0.7);
    font-size: var(--fs-md);
  }
</style>
