<script lang="ts">
  // The light for the camera (part of the image source): a lamp or the camera's flash for a dark
  // place. Stored in the sensor's triggers (light_entity, light_delay_s); limits from GET /config.
  import { app } from '../app.svelte';
  import type { Triggers } from '../types';
  import EntityPicker from './EntityPicker.svelte';

  let { triggers = $bindable(), lightError = '' }: { triggers: Triggers; lightError?: string } = $props();

  const limits = $derived(app.config?.trigger_limits);
  const lightDomains = $derived(app.config?.light_domains ?? []);
  // The picker works with a list; the light is at most one entity.
  let light = $state(triggers.light_entity ? [triggers.light_entity] : []);
  $effect(() => {
    triggers.light_entity = light[0] ?? '';
  });
</script>

<div class="col light-editor">
  <span class="col" style="gap:2px">
    <strong class="small">Light for the camera <span class="faint">(optional)</span></strong>
    <span class="xsmall faint">
      A lamp or the camera's flash, for a dark place. It is switched on for each check and while you look at live frames
      here (region, labelling), and off again afterwards; a light that is already on is left alone.
    </span>
  </span>
  <EntityPicker bind:value={light} max={1} domains={lightDomains} label="Light to switch on" placeholder="Search lights and switches" />
  {#if light.length && limits}
    <label class="line">
      <span class="small">Wait before taking the frame</span>
      <span class="row"
        ><input
          class="input sm num"
          type="number"
          min={limits.light_delay_s[0]}
          max={limits.light_delay_s[1]}
          step="0.5"
          bind:value={triggers.light_delay_s}
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
  p {
    margin: 0;
  }
</style>
