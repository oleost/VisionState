<script lang="ts">
  // The ESP32 guide: pick a board and what it is, wire it, put the configuration into ESPHome,
  // then check the device answers and make a sensor of it (docs/ESP32_PLAN.md, esphome/ in the
  // repository). The password and the API key are made up here and never leave the page until the
  // sensor is made with the address.
  import { untrack } from 'svelte';
  import { api } from '../lib/api';
  import { app, toast, toastError } from '../lib/app.svelte';
  import Icon from '../lib/components/Icon.svelte';
  import { prefill } from '../lib/prefill.svelte';
  import { go, href, paths } from '../lib/router.svelte';
  import type { EsphomeCheck, EsphomeConfiguration, EsphomeDevice } from '../lib/types';
  import { INMP441_WIRING } from '../lib/ui';

  const cfg = app.config!.esphome;
  const boardIds = Object.keys(cfg.boards);

  let board = $state(boardIds[0]);
  let kind = $state(cfg.boards[boardIds[0]].kinds[0]);
  let name = $state('');
  let mute = $state(false);
  let made = $state<EsphomeConfiguration | null>(null);
  let madeError = $state('');

  const spec = $derived(cfg.boards[board]);
  const microphone = $derived(kind === 'microphone' || kind === 'camera_microphone');
  const camera = $derived(kind === 'camera' || kind === 'camera_microphone');
  const friendly = $derived(name.trim() || 'Hallway');

  function randomChars(count: number): string {
    const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789';
    return Array.from(crypto.getRandomValues(new Uint32Array(count)), (n) => chars[n % chars.length]).join('');
  }
  const apiKey = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))));
  let password = $state(randomChars(cfg.password_chars));

  function pickBoard(id: string) {
    board = id;
    if (!cfg.boards[id].kinds.includes(kind)) kind = cfg.boards[id].kinds[0];
  }

  // The configuration follows every choice; the last answer counts.
  let loadCount = 0;
  $effect(() => {
    const args = [board, kind, friendly, microphone && mute] as const;
    untrack(() => load(...args));
  });
  async function load(b: string, k: string, n: string, m: boolean) {
    const mine = ++loadCount;
    try {
      const got = await api.esphomeConfiguration(b, k, n, m);
      if (mine !== loadCount) return;
      made = got;
      madeError = '';
    } catch (err) {
      if (mine === loadCount) madeError = (err as Error).message;
    }
  }

  // The user's values, filled into the file here in the browser: they never reach VisionState.
  let wifiSsid = $state('');
  let wifiPassword = $state('');
  let showWifiPassword = $state(false);
  const values = $derived({ wifi_ssid: wifiSsid, wifi_password: wifiPassword, stream_password: password, api_key: apiKey });
  const fill = (yaml: string, mask: boolean) =>
    yaml.replace(/\{\{(\w+)\}\}/g, (_, k: keyof typeof values) =>
      k === 'wifi_password' && mask && values[k] ? '"••••••••"' : JSON.stringify(values[k] ?? ''),
    );
  const filled = $derived(made ? fill(made.yaml, false) : '');
  const shown = $derived(made ? fill(made.yaml, true) : ''); // the Wi-Fi password masked
  const ready = $derived(wifiSsid.trim().length > 0 && wifiPassword.length > 0);
  // ESPHome reads ${...} in a value as a substitution.
  const dollar = $derived(wifiSsid.includes('$') || wifiPassword.includes('$'));


  function download() {
    const url = URL.createObjectURL(new Blob([filled], { type: 'text/yaml' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `${made!.name}.yaml`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // Step 4: the device on the network.
  let devices = $state<EsphomeDevice[]>([]);
  let devicesError = $state('');
  let host = $state('');
  let checking = $state(false);
  let checked = $state<EsphomeCheck | null>(null);

  async function findDevices() {
    try {
      const got = await api.esphomeDevices();
      devices = got.devices;
      devicesError = got.error ?? '';
    } catch (err) {
      devicesError = (err as Error).message;
    }
  }
  untrack(() => findDevices());

  async function check() {
    checking = true;
    checked = null;
    try {
      checked = await api.esphomeCheck(host.trim(), password, microphone, camera);
    } catch (err) {
      toastError(err);
    } finally {
      checking = false;
    }
  }

  function partText(part: EsphomeCheck['sound']): string {
    if (!part) return '';
    if (part.ok) return part.bytes ? `OK (a picture of ${Math.round(part.bytes / 1000)} kB)` : 'OK';
    if (part.status === 401) return 'Wrong password: give the one in the device\'s file (stream_password)';
    if (part.status === 409) return 'Busy: two listeners already (VLC, another VisionState)';
    if (part.status === 404) return 'Not in this firmware (another kind of device?)';
    return part.detail ?? `The device answered ${part.status}`;
  }

  function makeSensor(mode: 'sound' | 'picture') {
    const address = mode === 'sound' ? checked?.addresses.sound : checked?.addresses.picture;
    if (!address) return;
    prefill.newSensor = {
      mode,
      name: friendly.replace(/ (microphone|camera)$/i, ''),
      sourceType: mode === 'sound' ? 'rtsp' : 'http',
      source: address,
    };
    go(paths.newSensor());
  }
</script>

<div class="page" data-testid="esp32-guide">
  <p class="small"><a href={href(paths.newSensor())}>New sensor</a> / ESP32 device</p>
  <h2>An ESP32 microphone or camera of your own</h2>
  <p class="muted small">
    A cheap ESP32 board with ESPHome: a microphone for sound sensors, a camera for image sensors, or both. VisionState
    takes its sound and pictures straight from it, behind a password. This page writes what to put into ESPHome.
  </p>

  <section class="card pad col">
    <h3>1. The board and what it is</h3>
    <div class="col tight" role="radiogroup" aria-label="Board">
      {#each boardIds as id (id)}
        <label class="check">
          <input type="radio" name="board" checked={board === id} onchange={() => pickBoard(id)} />
          <span><strong>{cfg.boards[id].name}</strong> <span class="muted small">{cfg.boards[id].description}</span></span>
        </label>
      {/each}
    </div>
    <div class="row wrap" role="radiogroup" aria-label="What it is">
      {#each spec.kinds as k (k)}
        <label class="check">
          <input type="radio" name="kind" checked={kind === k} onchange={() => (kind = k)} />
          <Icon name={k === 'microphone' ? 'mic' : 'camera'} size={16} />
          {cfg.kinds[k]}
        </label>
      {/each}
    </div>
    <label class="field" style="max-width:420px">
      Device name
      <input class="input" bind:value={name} placeholder="Hallway" aria-label="Device name" />
    </label>
    {#if microphone}
      <label class="check">
        <input type="checkbox" bind:checked={mute} />
        <span>A mute switch on a pin (unwired, the microphone is on; closed, it is muted)</span>
      </label>
    {/if}
  </section>

  <section class="card pad col">
    <h3>2. Wire it</h3>
    {#if microphone}
      <p class="small">An INMP441 microphone, with short wires:</p>
      <div class="wiring">
        <table class="small">
          <thead><tr><th>INMP441</th><th>{spec.name}</th></tr></thead>
          <tbody>
            {#each INMP441_WIRING as row (row.mic)}
              <tr><td class="mono">{row.mic}</td><td class="mono">{row.pin ? spec.pins[row.pin] : row.board}</td></tr>
            {/each}
            {#if mute}
              <tr><td>mute switch</td><td class="mono">{spec.pins.mute_pin} to GND</td></tr>
            {/if}
          </tbody>
        </table>
      </div>
      {#if camera}
        <p class="xsmall muted">On the SD card's pins: leave the SD card out.</p>
      {/if}
    {:else}
      <p class="small">Nothing to wire: the camera is on the board.</p>
    {/if}
    {#if board === 'esp32-cam'}
      <p class="xsmall muted">
        Power it with 5 V that gives at least 1 A: a weak supply makes it restart when the camera or the Wi-Fi draws
        current. To install the first time it needs a USB adapter (an ESP32-CAM-MB, or a USB-to-serial adapter).
      </p>
    {/if}
    <p class="xsmall muted">{spec.light} lights while VisionState listens or looks.</p>
  </section>

  <section class="card pad col">
    <h3>3. Put it into ESPHome</h3>
    <div class="row wrap">
      <label class="field">
        Wi-Fi name
        <input class="input" bind:value={wifiSsid} aria-label="Wi-Fi name" autocomplete="off" autocapitalize="off"
          spellcheck="false" />
      </label>
      <label class="field">
        Wi-Fi password
        <input class="input" type={showWifiPassword ? 'text' : 'password'} bind:value={wifiPassword}
          aria-label="Wi-Fi password" autocomplete="off" />
      </label>
      <label class="field">
        Password for VisionState
        <input class="input mono" bind:value={password} aria-label="Device password" autocomplete="off"
          autocapitalize="off" spellcheck="false" />
      </label>
    </div>
    <div class="row wrap">
      <label class="check"><input type="checkbox" bind:checked={showWifiPassword} /> Show the Wi-Fi password</label>
      <button class="btn sm" onclick={() => (password = randomChars(cfg.password_chars))}>
        <Icon name="refresh" /> Another password
      </button>
    </div>
    <p class="xsmall muted">
      The Wi-Fi is the 2.4 GHz network the device joins. Only VisionState knows the device's password (it asks for the
      sound and the pictures with it); it is made up here, change it if you like. These stay in your browser: they go into
      the file below, not to VisionState.
    </p>
    {#if dollar}
      <p class="notice warn small">A $ in the Wi-Fi name or password: ESPHome may read it as a substitution.</p>
    {/if}
    {#if madeError}
      <p class="notice danger">{madeError}</p>
    {:else if made}
      <ol class="small steps">
        <li>Download the device's file:</li>
      </ol>
      <div class="row wrap">
        <button class="btn primary" disabled={!ready} onclick={download}><Icon name="download" /> Download {made.name}.yaml</button>
        {#if !ready}<span class="xsmall muted">Fill in the Wi-Fi first.</span>{/if}
      </div>
      <ol class="small steps" start="2">
        <li>
          In the ESPHome app: <strong>New device</strong> → <strong>Advanced set up options</strong> →
          <strong>Import from file</strong>, and pick it (or drop it on the page).
        </li>
      </ol>
      <details class="small">
        <summary>What is in it</summary>
        <textarea class="input mono code" readonly rows={shown.split('\n').length} value={shown}
          aria-label="ESPHome configuration"></textarea>
      </details>
      <ol class="small steps" start="3">
        <li>
          <strong>Install</strong>: the first time over USB (ESPHome offers its web page for that), then over Wi-Fi. The
          first build takes several minutes.
        </li>
        <li>Home Assistant finds the device: add it (Settings → Devices).</li>
      </ol>
    {/if}
  </section>

  <section class="card pad col">
    <h3>4. Check it and make the sensor</h3>
    {#if devices.length}
      <div class="col tight">
        <span class="small muted">Devices Home Assistant knows the address of:</span>
        <div class="row wrap">
          {#each devices as d (d.entity_id)}
            <button class="btn sm" class:primary={host === d.address} onclick={() => (host = d.address)}>
              {d.name} <span class="mono xsmall">{d.address}</span>
            </button>
          {/each}
        </div>
      </div>
    {:else}
      <p class="small muted">
        {devicesError ? `Could not ask Home Assistant: ${devicesError}.` : 'No device with an IP address in Home Assistant yet.'}
        Type its address, or look again once it is added.
      </p>
    {/if}
    <div class="row wrap">
      <label class="field">
        IP address
        <input class="input mono" bind:value={host} placeholder="192.168.1.50" aria-label="IP address"
          autocomplete="off" autocapitalize="off" spellcheck="false" />
      </label>
    </div>
    <div class="row wrap">
      <button class="btn primary" disabled={!host.trim() || !password || checking} onclick={check}>
        <Icon name="play" /> {checking ? 'Asking the device…' : 'Check'}
      </button>
      <button class="btn" onclick={findDevices}><Icon name="refresh" /> Look again</button>
    </div>
    {#if checked}
      <div class="col tight" data-testid="esp32-check">
        {#if checked.sound}
          <p class="small"><Icon name={checked.sound.ok ? 'check' : 'alert'} size={14} /> Sound: {partText(checked.sound)}</p>
        {/if}
        {#if checked.picture}
          <p class="small"><Icon name={checked.picture.ok ? 'check' : 'alert'} size={14} /> Picture: {partText(checked.picture)}</p>
        {/if}
      </div>
      <div class="row wrap">
        {#if checked.sound?.ok}
          <button class="btn primary" onclick={() => makeSensor('sound')}><Icon name="mic" /> Make a sound sensor</button>
        {/if}
        {#if checked.picture?.ok}
          <button class="btn primary" onclick={() => makeSensor('picture')}><Icon name="camera" /> Make an image sensor</button>
        {/if}
      </div>
    {/if}
  </section>
</div>

<style>
  .page > section {
    margin-top: var(--space-4);
  }
  .tight {
    gap: var(--space-2);
  }
  .steps {
    margin: 0;
    padding-left: 1.4em;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .code {
    height: auto; /* the rows, not the one-line height of an .input */
    padding: var(--space-2) var(--space-3);
    line-height: 1.5;
    font-family: var(--font-mono); /* .input sets the body font */
    font-size: var(--fs-xs);
    white-space: pre;
    overflow-x: auto;
    resize: vertical;
    width: 100%;
    box-sizing: border-box;
  }
  .wiring {
    overflow-x: auto;
  }
  table {
    border-collapse: collapse;
  }
  th,
  td {
    text-align: left;
    padding: var(--space-1) var(--space-3);
    border-bottom: 1px solid var(--c-border);
  }
</style>
