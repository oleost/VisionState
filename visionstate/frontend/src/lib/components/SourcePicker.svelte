<script lang="ts">
  // Pick an image source: a Home Assistant camera, or a direct HTTP/RTSP URL. With `sound`, a sound
  // sensor's source: a camera that streams in Home Assistant (its own stream, with its sound), or a
  // stream address (a camera's RTSP stream, a go2rtc restream, an ESP32 microphone). An ESP32 of ours
  // that Home Assistant has is offered first: its stream's password, checked, gives its address. The
  // device tells the password through Home Assistant (firmware 1.3.0 on); an older one's is typed.
  import { onMount } from 'svelte';
  import { api } from '../api';
  import { app } from '../app.svelte';
  import { checkText } from '../esp32';
  import { href, paths } from '../router.svelte';
  import type { Camera, EsphomeCheck, EsphomeDevice } from '../types';

  let {
    sourceType = $bindable('ha_camera'),
    source = $bindable(''),
    sound = false,
    ondone,
    onesp32,
  }: {
    sourceType: string;
    source: string;
    sound?: boolean;
    /** A URL has been typed (the field was left, or Enter), or a type picked with a URL in it. */
    ondone?: () => void;
    /** An ESP32 of ours was picked: its device (its flash, for the light) and the address used. */
    onesp32?: (device: EsphomeDevice, address: string) => void;
  } = $props();

  let cameras = $state<Camera[]>([]);
  let error = $state('');
  let loading = $state(true);
  let urlSource = $state(sourceType === 'ha_camera' ? '' : source);
  const urlTypes = $derived(
    sound
      ? [['rtsp', 'Stream URL']]
      : Object.entries(app.config?.source_types ?? {}).filter(([k]) => k !== 'ha_camera'),
  );
  // A sound sensor listens to a camera's stream: one without (snapshots only) cannot be picked.
  const fits = (cam: Camera) => !sound || cam.stream;

  // ESP32s of ours with a microphone (sound) or a camera (pictures).
  let esp32s = $state<EsphomeDevice[]>([]);
  let picking = $state<EsphomeDevice | null>(null);
  let password = $state('');
  let connecting = $state(false);
  let asking = $state(false); // asking the device for its password: no field until it says it cannot
  let espError = $state('');
  const hostOf = (url: string) => {
    try {
      return new URL(url).hostname;
    } catch {
      return '';
    }
  };
  const espActive = (d: EsphomeDevice) =>
    picking ? picking.entity_id === d.entity_id : sourceType !== 'ha_camera' && hostOf(source) === d.address;

  async function pickEsp(d: EsphomeDevice) {
    picking = d;
    password = '';
    espError = '';
    asking = true;
    try {
      // An older firmware does not tell its password (or Home Assistant could not ask): it is typed.
      const checked = await api.esphomeConnect(d.entity_id, sound, !sound);
      if (checked.told) use(d, checked);
    } catch {
      // Not reachable: typed as well
    } finally {
      if (picking === d) asking = false;
    }
  }

  // The check's answer: the address taken, or why not (shown at the password field).
  function use(device: EsphomeDevice, checked: EsphomeCheck) {
    if (picking !== device) return; // another source was picked meanwhile
    const part = sound ? checked.sound : checked.picture;
    const address = sound ? checked.addresses.sound : checked.addresses.picture;
    if (!part?.ok || !address) {
      espError = checkText(part);
      return;
    }
    sourceType = sound ? 'rtsp' : 'http'; // a sound sensor's stream address, a camera's snapshot URL
    source = urlSource = address;
    picking = null;
    onesp32?.(device, address);
    ondone?.();
  }

  async function connect() {
    if (!picking) return;
    connecting = true;
    espError = '';
    try {
      use(picking, await api.esphomeCheck(picking.address, password, sound, !sound));
    } catch (err) {
      espError = (err as Error).message;
    } finally {
      connecting = false;
    }
  }

  onMount(async () => {
    api
      .esphomeDevices()
      .then((got) => (esp32s = got.devices.filter((d) => d.ours && (sound ? d.microphone : d.camera))))
      .catch(() => {}); // Home Assistant not reachable: its cameras say so below
    try {
      cameras = await api.cameras();
      const first = cameras.find(fits);
      if (!source && sourceType === 'ha_camera') {
        if (first) source = first.entity_id;
        else if (urlTypes.length === 1) sourceType = urlTypes[0][0]; // nothing to pick: the address field at once
      }
    } catch (err) {
      error = (err as Error).message;
    } finally {
      loading = false;
    }
  });

  function pickCamera(id: string) {
    picking = null;
    sourceType = 'ha_camera';
    source = id;
  }

  function pickUrl(type: string) {
    picking = null;
    sourceType = type;
    source = urlSource;
    ondone?.();
  }
</script>

<fieldset class="col">
  <legend class="field">{sound ? 'Sound source' : 'Image source'}</legend>
  {#if loading}
    <p class="muted small">Loading cameras from Home Assistant…</p>
  {:else if error}
    <p class="notice warn">Could not list Home Assistant cameras: {error}</p>
  {:else if !cameras.some(fits)}
    <p class="muted small">
      {sound ? 'No camera in Home Assistant has a stream.' : 'No camera entities found in Home Assistant.'} Use a URL below
      instead.
    </p>
  {:else if sound}
    <p class="xsmall muted">
      A camera's own stream in Home Assistant, with its sound: several seconds behind the camera (its RTSP stream below
      is not), and it takes a while to start. Switch the camera's microphone on first.
    </p>
  {/if}

  {#if esp32s.length}
    <div class="cams" data-testid="esp32-sources">
      {#each esp32s as d (d.entity_id)}
        <label class="cam" class:active={espActive(d)}>
          <input type="radio" name="source" checked={espActive(d)} onchange={() => pickEsp(d)} />
          <span class="col cam-text">
            <span>{d.name}</span>
            <span class="mono xsmall muted">{d.address} · VisionState ESP32</span>
          </span>
        </label>
      {/each}
    </div>
    {#if picking && asking}
      <p class="muted small" data-testid="esp32-asking">Asking {picking.name} for its stream password…</p>
    {:else if picking}
      <form
        class="url card pad col"
        onsubmit={(e) => {
          e.preventDefault();
          connect();
        }}
      >
        <label class="field small">
          The stream password of {picking.name}
          <input
            class="input"
            type="password"
            bind:value={password}
            autocomplete="off"
            aria-label="Stream password"
          />
        </label>
        <span class="xsmall muted">
          <span class="mono">stream_password</span> in the device's file (its firmware is older than 1.3.0, which tells
          it to VisionState through Home Assistant: choose <i>Install</i> for it in the ESPHome app). VisionState then
          takes its {sound ? 'sound' : 'pictures'} straight from it: fresh, without going through Home Assistant.
        </span>
        <div class="row">
          <button class="btn sm primary" type="submit" disabled={!password || connecting}>
            {connecting ? 'Checking…' : 'Use it'}
          </button>
        </div>
        {#if espError}<p class="notice warn small">{espError}</p>{/if}
      </form>
    {/if}
  {/if}

  <div class="cams">
    {#each cameras as cam (cam.entity_id)}
      <label
        class="cam"
        class:active={!picking && sourceType === 'ha_camera' && source === cam.entity_id}
        class:off={!fits(cam)}
      >
        <input
          type="radio"
          name="source"
          checked={!picking && sourceType === 'ha_camera' && source === cam.entity_id}
          disabled={!fits(cam)}
          onchange={() => pickCamera(cam.entity_id)}
        />
        <span class="col cam-text">
          <span>{cam.name}</span>
          <span class="mono xsmall muted">{cam.entity_id}</span>
        </span>
        {#if !fits(cam)}<span class="chip">no stream</span>
        {:else if cam.state === 'unavailable'}<span class="chip danger">unavailable</span>{/if}
      </label>
    {/each}
  </div>

  <div class="url card pad col">
    <div class="row wrap">
      {#each urlTypes as [type, label] (type)}
        <label class="check">
          <input type="radio" name="source" checked={!picking && sourceType === type} onchange={() => pickUrl(type)} />
          {label}
        </label>
      {/each}
    </div>
    {#if sourceType !== 'ha_camera'}
      <input
        class="input"
        placeholder={sourceType === 'rtsp' ? 'rtsp://user:pass@192.168.1.20:554/stream1' : 'http://192.168.1.20/snapshot.jpg'}
        bind:value={urlSource}
        oninput={() => (source = urlSource)}
        onblur={() => ondone?.()}
        onkeydown={(e) => e.key === 'Enter' && ondone?.()}
        aria-label={sound ? 'Stream with sound' : 'Source URL'}
        autocomplete="off"
        autocapitalize="off"
        spellcheck="false"
      />
      {#if sound}
        <p class="xsmall muted">
          A camera's RTSP stream (switch its microphone on in the camera), a go2rtc restream
          (<span class="mono">rtsp://homeassistant.local:8554/name</span>: one connection to the camera for everyone), or an
          HTTP audio stream such as an ESP32 microphone sending WAV (<span class="mono">http://esp32.local/sound.wav</span>).
          A camera's sub stream is enough: the sound is the same, and clips with video stay small.
        </p>
      {/if}
    {/if}
  </div>
  <p class="xsmall muted" data-testid="esp32-link">
    An ESP32 {sound ? 'microphone' : 'camera'} of your own? <a href={href(paths.esp32())}>Set one up with ESPHome</a>
  </p>
</fieldset>

<style>
  fieldset {
    border: none;
    margin: 0;
    padding: 0;
  }
  legend {
    padding: 0;
    margin-bottom: var(--space-2);
  }
  .cams {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: var(--space-3);
  }
  .cam {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-3);
    border-radius: var(--radius-lg);
    background: var(--c-bg);
    border: 1px solid var(--c-border);
    cursor: pointer;
  }
  .cam-text {
    gap: 2px;
    min-width: 0; /* long names and entity IDs wrap inside the box */
    overflow-wrap: anywhere;
  }
  .cam.active {
    border-color: var(--c-accent);
  }
  .cam.off {
    cursor: default;
    opacity: 0.6;
  }
  .cam input,
  .url input[type='radio'] {
    accent-color: var(--c-accent);
    width: 18px;
    height: 18px;
  }
  .url {
    background: var(--c-bg);
    border-style: dashed;
  }
</style>
