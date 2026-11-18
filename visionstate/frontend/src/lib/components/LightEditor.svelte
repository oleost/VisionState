<script lang="ts">
  // The light for the camera (part of the image source): a lamp or the camera's flash for a dark
  // place, for every check or only when the frame is dark. Stored in the sensor's triggers
  // (light_entity, light_delay_s, light_when); limits and choices from GET /config. An ESP32 camera
  // of ours offers its flash, and the editor says how the light chosen is switched: by the camera
  // itself for its picture, or through Home Assistant, and why (POST camera-light).
  import { untrack } from 'svelte';
  import { api } from '../api';
  import { app } from '../app.svelte';
  import type { CameraLight, Triggers } from '../types';
  import EntityPicker from './EntityPicker.svelte';

  let {
    triggers = $bindable(),
    lightError = '',
    sourceType = '',
    source = '',
    offered = null,
  }: {
    triggers: Triggers;
    lightError?: string;
    sourceType?: string;
    source?: string;
    /** The flash of an ESP32 the wizard was given (before Home Assistant is asked). */
    offered?: string | null;
  } = $props();

  const limits = $derived(app.config?.trigger_limits);
  const esphome = $derived(app.config?.esphome);
  const lightDomains = $derived(app.config?.light_domains ?? []);
  const lightWhen = $derived(Object.entries(app.config?.light_when ?? {}));
  // The picker works with a list; the light is at most one entity.
  let light = $state(triggers.light_entity ? [triggers.light_entity] : []);
  $effect(() => {
    triggers.light_entity = light[0] ?? '';
  });

  // Asked again a moment after the source or the light changes; an answer to an older question is dropped.
  let info = $state<CameraLight | null>(null);
  let asked = 0;
  $effect(() => {
    const question = [sourceType, source, light[0] ?? ''] as const;
    const n = ++asked;
    const timer = setTimeout(() => untrack(() => ask(n, ...question)), 400);
    return () => clearTimeout(timer);
  });
  async function ask(n: number, type: string, src: string, chosen: string) {
    if (!type || (!src && !chosen)) {
      info = null;
      return;
    }
    try {
      const found = await api.cameraLight(type, src, chosen);
      if (n === asked) info = found;
    } catch {
      if (n === asked) info = null; // nothing to say about the light; it works as before
    }
  }

  const flash = $derived(info?.flash ?? offered);
  const own = $derived(!!light.length && info?.light === 'own');
  const delayMax = $derived(
    limits ? (own && esphome ? Math.min(limits.light_delay_s[1], esphome.flash_max_s) : limits.light_delay_s[1]) : 0,
  );
  function useFlash() {
    light = [flash!];
  }
</script>

<div class="col light-editor">
  <span class="col" style="gap:2px">
    <strong class="small">Light for the camera <span class="faint">(optional)</span></strong>
    <span class="xsmall faint">
      A lamp or the camera's flash, for a dark place. It is switched on for each check (or only when the picture is
      dark) and while you look at live frames here (region, labelling), and off again afterwards; a light that is
      already on is left alone.
    </span>
  </span>
  {#if flash && light[0] !== flash}
    <p class="small offer" data-testid="camera-flash-offer">
      <span>{info?.device ?? 'The camera'} has its own flash (<span class="mono">{flash}</span>).</span>
      <button type="button" class="btn sm" onclick={useFlash}>Use the camera's flash</button>
    </p>
  {/if}
  <EntityPicker bind:value={light} max={1} domains={lightDomains} label="Light to switch on" placeholder="Search lights and switches" />
  {#if light.length && info?.light}
    {@const device = info.light_device ?? 'the camera'}
    {#if info.light === 'own'}
      <p class="xsmall status" data-testid="light-status">
        The camera's own flash: the camera switches it on for each picture itself and off after it. Nothing goes through
        Home Assistant, so the picture is lit even while the device is away from Home Assistant.
      </p>
    {:else if info.light === 'firmware'}
      <div class="notice warn small" data-testid="light-status">
        <span
          >The flash of {device}: its firmware{info.firmware ? ` (${info.firmware})` : ''} cannot take a picture with
          it, so it is switched through Home Assistant. Install firmware {esphome?.flash_firmware} or newer (choose
          <em>Install</em> for the device in the ESPHome app) for the camera to switch it itself.</span
        >
      </div>
    {:else if info.light === 'camera_entity'}
      <div class="notice small" data-testid="light-status">
        <span
          >The flash of {device}, and this sensor looks through its camera entity: the flash is switched through Home
          Assistant. Choose the device itself as the source (your ESP32 in the list above) for the camera to take the
          picture with its flash.</span
        >
      </div>
    {:else if info.light === 'other_source'}
      <div class="notice warn small" data-testid="light-status">
        <span
          >The flash of {device}, but this sensor does not take its pictures from that device's address in Home
          Assistant: the flash is switched through Home Assistant. If it is that camera, use the address Home Assistant
          shows for it.</span
        >
      </div>
    {/if}
  {/if}
  {#if light.length && limits}
    <label class="line">
      <span class="small">Switch it on</span>
      <select class="input sm" bind:value={triggers.light_when} aria-label="When the light is switched on">
        {#each lightWhen as [key, name] (key)}<option value={key}>{name}</option>{/each}
      </select>
    </label>
    <label class="line">
      <span class="col" style="gap:0">
        <span class="small">{own ? 'Flash on before the picture' : 'Wait before taking the frame'}</span>
        {#if own}<span class="xsmall faint">The camera takes the picture this long after the flash came on.</span>{/if}
      </span>
      <span class="row"
        ><input
          class="input sm num"
          type="number"
          min={limits.light_delay_s[0]}
          max={delayMax}
          step="0.5"
          bind:value={triggers.light_delay_s}
          aria-label={own ? 'Flash on before the picture' : 'Wait before taking the frame'}
        /> s</span
      >
    </label>
    {#if triggers.change_detection}
      <p class="xsmall faint">Change detection compares frames without the light.</p>
    {/if}
    {#if lightError}<div class="notice warn small">Could not switch the light: {lightError}</div>{/if}
  {/if}
</div>

<style>
  .light-editor {
    gap: var(--space-2);
  }
  .line {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-4);
    color: var(--c-text-2);
  }
  .line select {
    width: auto;
    min-width: 0;
  }
  .offer {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-2);
    overflow-wrap: anywhere;
  }
  .status {
    color: var(--c-text-2);
  }
  p {
    margin: 0;
  }
</style>
