<script lang="ts">
  // What a reading sensor reads: the type (digital display or mechanical counter), mode, decimals,
  // unit, Home Assistant device class and display details.
  // Options and limits come from the backend config.
  import { app } from '../app.svelte';
  import { decimalsSeen, readingType } from '../reading';
  import type { ReadingMode, ReadingSettings, ReadingType } from '../types';
  import { READING_DISPLAY_INFO, READING_MODE_INFO, READING_TYPE_INFO, READING_TYPE_MODES, UNIT_DEVICE_CLASS } from '../ui';

  // seen: the text the reader last saw (a test read), to suggest the number of decimals.
  let { value = $bindable(), seen = null }: { value: ReadingSettings; seen?: string | null } = $props();

  const type = $derived(readingType(value));
  const types = Object.keys(READING_TYPE_INFO) as ReadingType[];
  const modes = $derived((app.config?.reading_modes ?? []).filter((m) => READING_TYPE_MODES[type].includes(m)));
  // The display details of a digital display; a mechanical counter is chosen as the type above.
  const displays = $derived((app.config?.reading_displays ?? []).filter((d) => d !== 'counter'));
  const digitLimits = $derived(app.config?.reading_limits.digits ?? [1, 12]);
  const deviceClasses = $derived(app.config?.reading_device_classes ?? []);
  const decimals = $derived(app.config?.reading_limits.decimals ?? [0, 4]);
  const suggested = $derived.by(() => {
    const n = decimalsSeen(seen);
    return n !== null && n !== value.decimals && n >= decimals[0] && n <= decimals[1] ? n : null;
  });
  // A mechanical counter shows all its wheels, so the example has as many digits as it has wheels.
  const exampleDigits = $derived(type === 'counter' ? '123456789012'.slice(0, Math.max(value.decimals + 1, value.digits)) : '1234567');
  const example = $derived((Number(exampleDigits) / 10 ** value.decimals).toFixed(value.decimals));

  function setType(next: ReadingType) {
    if (next === type) return;
    if (next === 'counter') {
      value.display = 'counter';
      if (!READING_TYPE_MODES.counter.includes(value.mode)) setMode('counter');
    } else {
      value.display = app.config?.reading_defaults.display ?? 'auto';
    }
  }

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
  <div class="col group">
    <span class="group-label">What does it look like?</span>
    <div class="modes two" role="radiogroup" aria-label="What it looks like">
      {#each types as t (t)}
        <button class="mode" class:selected={type === t} role="radio" aria-checked={type === t} onclick={() => setType(t)}>
          <strong>{READING_TYPE_INFO[t].title}</strong>
          <span class="xsmall muted">{READING_TYPE_INFO[t].text}</span>
          <span class="xsmall faint">{READING_TYPE_INFO[t].example}</span>
        </button>
      {/each}
    </div>
  </div>

  {#if type === 'counter'}
    <label class="field" style="max-width:360px">
      Number of digits
      <input class="input" type="number" min={digitLimits[0]} max={digitLimits[1]} step="1" bind:value={value.digits} />
      <span class="hint">Count every wheel inside the region, coloured ones too. The region is split into that many equal fields.</span>
    </label>
  {/if}

  <span class="group-label">What is the number?</span>
  <div class="modes" class:two={modes.length === 2} role="radiogroup" aria-label="What the number is">
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
        <span class="hint"
          >{type === 'counter' ? 'The wheels show' : 'The display shows'} e.g. <span class="mono">{exampleDigits}</span> →
          <span class="mono">{example}</span>{type === 'counter' && value.decimals ? ' — often the coloured wheels' : ''}</span
        >
      </label>
      {#if suggested !== null}
        <div class="notice warn small suggest">
          <span>The display shows <span class="mono">“{seen}”</span> — {suggested} digit{suggested === 1 ? '' : 's'} after the point?</span>
          <button type="button" class="btn sm" onclick={() => (value.decimals = suggested!)}>Use {suggested}</button>
        </div>
      {/if}
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

  {#if type === 'display'}
    <label class="field" style="max-width:360px">
      Display
      <select class="input" bind:value={value.display}>
        {#each displays as d (d)}<option value={d}>{READING_DISPLAY_INFO[d]}</option>{/each}
      </select>
      <span class="hint">Pick LED or LCD if faint, unlit segments are read as digits (a 3 read as 8).</span>
    </label>
  {:else}
    <p class="small muted">
      While a wheel is turning its digit can be misread. A counter that reads lower than before is rejected; under
      <em>Sensor output</em> you can also limit how much it may change at once.
    </p>
  {/if}
</div>

<style>
  .editor {
    gap: var(--space-4);
  }
  .group {
    gap: var(--space-2);
  }
  .group-label {
    font-size: var(--fs-md);
    font-weight: 600;
    color: var(--c-text-2);
  }
  .modes {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: var(--space-2);
  }
  .modes.two {
    grid-template-columns: repeat(2, minmax(0, 1fr));
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
  .suggest {
    grid-column: 1 / -1;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: var(--space-2);
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: var(--space-3);
  }
  @media (max-width: 760px) {
    .modes,
    .modes.two,
    .grid {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
