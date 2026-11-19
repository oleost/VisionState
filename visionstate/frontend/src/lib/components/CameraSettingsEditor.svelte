<script lang="ts">
  // How an ESP32 camera of ours is set up for this sensor's pictures (stored in the sensor's triggers:
  // camera; null is its kind's preset), sent with each snapshot. A preset fills the six settings in;
  // the name shown is the preset whose values they are, or Custom. Choices and limits from GET /config.
  import { app } from '../app.svelte';
  import type { CameraSettings, SensorCamera, Triggers } from '../types';

  let {
    triggers = $bindable(),
    kind,
    camera,
  }: {
    triggers: Triggers;
    kind: string;
    /** What the camera said of itself (sensor.camera). */
    camera: SensorCamera;
  } = $props();

  const cfg = $derived(app.config?.camera);
  const kindPreset = $derived(cfg ? cfg.presets[cfg.preset_by_kind[kind] ?? 'states'] : null);
  const settings = $derived<CameraSettings | null>(triggers.camera ?? kindPreset?.settings ?? null);
  const same = (a: CameraSettings, b: CameraSettings) =>
    a.size === b.size &&
    a.quality === b.quality &&
    a.clock_mhz === b.clock_mhz &&
    a.exposure_ms === b.exposure_ms &&
    a.gain === b.gain;
  const preset = $derived(
    settings && cfg ? (Object.entries(cfg.presets).find(([, p]) => same(p.settings, settings))?.[0] ?? '') : '',
  );
  const fixedDefault = $derived(cfg?.fixed_exposure_ms ?? 1);

  function choosePreset(key: string) {
    const chosen = cfg?.presets[key];
    if (chosen) triggers.camera = { ...chosen.settings };
  }
  function change(part: Partial<CameraSettings>) {
    if (settings) triggers.camera = { ...settings, ...part };
  }
  function describe(s: CameraSettings): string {
    const exposure = s.exposure_ms ? `exposure ${s.exposure_ms} ms` : 'automatic exposure';
    const size = s.size.replace('x', '×');
    return `${size} · quality ${s.quality} · ${s.clock_mhz} MHz · ${exposure} · gain up to ${s.gain}×`;
  }
</script>

{#if cfg && settings}
  <div class="col camera-settings" data-testid="camera-settings">
    <span class="col" style="gap:2px">
      <strong class="small">Camera set-up</strong>
      <span class="xsmall faint">
        A VisionState ESP32 camera is set up so before each of this sensor's pictures. A preset fills the settings in;
        change any of them and it is Custom.
      </span>
    </span>
    <label class="line">
      <span class="small">Preset</span>
      <select
        class="input sm"
        value={preset}
        aria-label="Camera preset"
        data-testid="camera-preset"
        onchange={(e) => choosePreset((e.currentTarget as HTMLSelectElement).value)}
      >
        {#each Object.entries(cfg.presets) as [key, p] (key)}<option value={key}>{p.name}</option>{/each}
        <option value="" disabled>Custom</option>
      </select>
    </label>
    {#if preset}<span class="xsmall faint">{cfg.presets[preset].description}.</span>{/if}
    <label class="line">
      <span class="small">Picture size</span>
      <select
        class="input sm"
        value={settings.size}
        aria-label="Picture size"
        onchange={(e) => change({ size: (e.currentTarget as HTMLSelectElement).value })}
      >
        {#each cfg.sizes as size (size)}<option value={size}>{size.replace('x', '×')}</option>{/each}
      </select>
    </label>
    <label class="line">
      <span class="col" style="gap:0">
        <span class="small">JPEG quality</span>
        <span class="xsmall faint">Lower is sharper and larger.</span>
      </span>
      <input
        class="input sm num"
        type="number"
        min={cfg.limits.quality[0]}
        max={cfg.limits.quality[1]}
        step="1"
        value={settings.quality}
        aria-label="JPEG quality"
        onchange={(e) => change({ quality: Number((e.currentTarget as HTMLInputElement).value) })}
      />
    </label>
    <label class="line">
      <span class="col" style="gap:0">
        <span class="small">Camera clock</span>
        <span class="xsmall faint"
          >Slower: fewer pictures a second, longer exposures, and no thin lines across large detailed pictures.</span
        >
      </span>
      <select
        class="input sm"
        value={String(settings.clock_mhz)}
        aria-label="Camera clock"
        onchange={(e) => change({ clock_mhz: Number((e.currentTarget as HTMLSelectElement).value) })}
      >
        {#each cfg.clocks_mhz as mhz (mhz)}<option value={String(mhz)}>{mhz} MHz</option>{/each}
      </select>
    </label>
    <label class="line">
      <span class="small">Exposure</span>
      <select
        class="input sm"
        value={settings.exposure_ms ? 'fixed' : 'auto'}
        aria-label="Exposure"
        onchange={(e) =>
          change({ exposure_ms: (e.currentTarget as HTMLSelectElement).value === 'fixed' ? fixedDefault : 0 })}
      >
        <option value="auto">Automatic</option>
        <option value="fixed">Fixed</option>
      </select>
    </label>
    {#if settings.exposure_ms}
      <label class="line">
        <span class="col" style="gap:0">
          <span class="small">Exposure time</span>
          <span class="xsmall faint">About: short keeps what moves sharp; the gain keeps it bright.</span>
        </span>
        <span class="row"
          ><input
            class="input sm num"
            type="number"
            min={cfg.limits.exposure_ms[0]}
            max={cfg.limits.exposure_ms[1]}
            step="1"
            value={settings.exposure_ms}
            aria-label="Exposure time"
            onchange={(e) => change({ exposure_ms: Number((e.currentTarget as HTMLInputElement).value) })}
          /> ms</span
        >
      </label>
    {/if}
    <label class="line">
      <span class="col" style="gap:0">
        <span class="small">Gain ceiling</span>
        <span class="xsmall faint">Higher is brighter in poor light, and noisier.</span>
      </span>
      <select
        class="input sm"
        value={String(settings.gain)}
        aria-label="Gain ceiling"
        onchange={(e) => change({ gain: Number((e.currentTarget as HTMLSelectElement).value) })}
      >
        {#each cfg.gains as gain (gain)}<option value={String(gain)}>{gain}×</option>{/each}
      </select>
    </label>
    {#if !camera.settings}
      <div class="notice warn small" data-testid="camera-settings-firmware">
        <span
          >This camera's firmware takes only the presets{preset ? '' : ', so these settings are not used'}. Install
          firmware 1.5.0 or newer (choose <em>Install</em> for the device in the ESPHome app) for the camera to take
          every setting.</span
        >
      </div>
    {/if}
    {#if camera.others.length}
      <div class="notice small" data-testid="camera-settings-others">
        <span
          >{camera.others.join(', ')} on this camera {camera.others.length === 1 ? 'uses' : 'use'} other settings: the
          camera is set up again before each picture, which takes a little longer.</span
        >
      </div>
    {/if}
    {#if camera.now}
      <span class="xsmall faint" data-testid="camera-settings-now"
        >Now: {describe(camera.now)}{camera.frame_ms ? ` · a picture every ${camera.frame_ms} ms` : ''}{camera.chip
          ? ` · camera chip ${camera.chip}`
          : ''}.</span
      >
    {/if}
  </div>
{/if}

<style>
  .camera-settings {
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
    flex: none; /* the text beside it wraps on a phone, the choice stays whole */
  }
</style>
