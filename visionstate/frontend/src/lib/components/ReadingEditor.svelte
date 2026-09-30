<script lang="ts">
  // What a reading sensor reads: mode, decimals, unit, Home Assistant device class and display.
  // Options and limits come from the backend config.
  import { app } from '../app.svelte';
  import type { ReadingMode, ReadingSettings } from '../types';
  import { READING_DISPLAY_INFO, READING_MODE_INFO, UNIT_DEVICE_CLASS } from '../ui';

  let { value = $bindable() }: { value: ReadingSettings } = $props();

  const modes = $derived(app.config?.reading_modes ?? []);
  const displays = $derived(app.config?.reading_displays ?? []);
  const deviceClasses = $derived(app.config?.reading_device_classes ?? []);
  const decimals = $derived(app.config?.reading_limits.decimals ?? [0, 4]);
  const example = $derived((1234567 / 10 ** value.decimals).toFixed(value.decimals));

  function setMode(mode: ReadingMode) {
    value.mode = mode;
    if (mode === 'time_left') value.device_class = 'duration';
    else if (value.device_class === 'duration') value.device_class = '';
  }

  function setUnit(unit: string) {
    value.unit = unit;
    // Suggest a device class for well-known units, without overriding a deliberate choice.
    const suggested = UNIT_DEVICE_CLASS[unit.trim()];
    if (suggested && !value.device_class) value.device_class = suggested;
  }
</script>

<div class="col editor">
  <div class="modes" role="radiogroup" aria-label="What the number is">
    {#each modes as mode (mode)}
      <button class="mode" class:selected={value.mode === mode} role="radio" aria-checked={value.mode === mode} onclick={() => setMode(mode)}>
        <strong>{READING_MODE_INFO[mode].title}</strong>
        <span class="xsmall muted">{READING_MODE_INFO[mode].text}</span>
      </button>
    {/each}
  </div>

  {#if value.mode !== 'time_left'}
    <div class="grid">
      <label class="field">
        Digits after the decimal point
        <input class="input" type="number" min={decimals[0]} max={decimals[1]} step="1" bind:value={value.decimals} />
        <span class="hint">The display shows e.g. <span class="mono">{example.replace('.', '')}</span> → <span class="mono">{example}</span></span>
      </label>
      <label class="field">
        Unit
        <input
          class="input"
          list="reading-units"
          value={value.unit}
          oninput={(e) => setUnit(e.currentTarget.value)}
          placeholder={READING_MODE_INFO[value.mode].units[0] ?? ''}
        />
        <datalist id="reading-units">
          {#each READING_MODE_INFO[value.mode].units as u (u)}<option value={u}></option>{/each}
        </datalist>
      </label>
      <label class="field">
        Home Assistant type
        <select class="input" bind:value={value.device_class}>
          {#each deviceClasses.filter((c) => c !== 'duration') as c (c)}
            <option value={c}>{c ? c.charAt(0).toUpperCase() + c.slice(1) : 'None'}</option>
          {/each}
        </select>
        <span class="hint">Energy, water and gas counters appear in the Energy dashboard.</span>
      </label>
    </div>
  {:else}
    <p class="small muted">Reads <span class="mono">1:25</span> as 85 minutes and <span class="mono">45</span> as 45 minutes.</p>
  {/if}

  <label class="field" style="max-width:360px">
    Display
    <select class="input" bind:value={value.display}>
      {#each displays as d (d)}<option value={d}>{READING_DISPLAY_INFO[d]}</option>{/each}
    </select>
    <span class="hint">Pick LED or LCD if faint, unlit segments are read as digits (a 3 read as 8).</span>
  </label>
</div>

<style>
  .editor {
    gap: var(--space-4);
  }
  .modes {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: var(--space-2);
  }
  .mode {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
    padding: var(--space-3);
    border-radius: var(--radius-md);
    border: 1px solid var(--c-border-strong);
    background: var(--c-surface);
    color: var(--c-text);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .mode.selected {
    border-color: var(--c-accent);
    box-shadow: 0 0 0 1px var(--c-accent);
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: var(--space-3);
  }
  @media (max-width: 760px) {
    .modes,
    .grid {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
