<script lang="ts">
  // The ESP32 guide: pick a board and what it is, wire it, put the configuration into ESPHome,
  // then check the device answers and make a sensor of it (docs/ESP32_PLAN.md, esphome/ in the
  // repository). The password and the API key are made up here and never leave the page until the
  // sensor is made with the address.
  import { onDestroy, untrack } from 'svelte';
  import { api } from '../lib/api';
  import { app, toast, toastError } from '../lib/app.svelte';
  import Icon from '../lib/components/Icon.svelte';
  import { prefill } from '../lib/prefill.svelte';
  import { go, href, paths } from '../lib/router.svelte';
  import type { EsphomeCheck, EsphomeConfiguration, EsphomeDevice } from '../lib/types';
  import { pollWhileVisible } from '../lib/poll';
  import { INMP441_WIRING, POLL } from '../lib/ui';

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
  let waiting = $state<string[]>([]);
  let devicesError = $state('');
  let host = $state('');
  let checking = $state(false);
  let checked = $state<EsphomeCheck | null>(null);
  let showKey = $state(false);

  // This device, by its name: Home Assistant calls its IP address entity after the friendly name.
  const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
  const ours = $derived(devices.find((d) => d.entity_id === `sensor.${slug(friendly)}_ip_address`) ?? null);
  const ourWaiting = $derived(made !== null && waiting.includes(made.name));

  async function findDevices() {
    try {
      const got = await api.esphomeDevices();
      devices = got.devices;
      waiting = got.waiting;
      devicesError = got.error ?? '';
    } catch (err) {
      devicesError = (err as Error).message;
    }
  }
  // Until it shows up: look again every few seconds while the page is open.
  onDestroy(pollWhileVisible(POLL.esp32Devices, () => untrack(() => findDevices())));
  $effect(() => {
    if (ours && !host) host = ours.address; // the new device's address, as soon as Home Assistant has it
  });

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
    // Not the device's own name: Home Assistant names the sensor's entities after it, next to the
    // device's (switch.hallway_enabled and switch.hallway_enabled_2), and the two sensors look alike.
    const base = friendly.replace(/ (microphone|camera)$/i, '');
    prefill.newSensor = {
      mode,
      name: `${base} ${mode === 'sound' ? 'sound' : 'camera'}`,
      sourceType: mode === 'sound' ? 'rtsp' : 'http',
      source: address,
      light: mode === 'picture' ? (devices.find((d) => d.address === host.trim())?.light ?? null) : null,
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
    {#if ours}
      <p class="notice ok small" data-testid="esp32-found">
        <Icon name="check" size={14} /> {ours.name} is in Home Assistant, at <span class="mono">{ours.address}</span>.
      </p>
    {:else if ourWaiting}
      <div class="notice warn col tight" data-testid="esp32-waiting">
        <strong class="small">Home Assistant has found {made?.name}: add it.</strong>
        <span class="small">
          Settings → Devices &amp; services: it is under <strong>Discovered</strong>. Press <strong>Add</strong>; it asks for
          the device's encryption key:
        </span>
        <div class="row wrap">
          <span class="mono xsmall key">{showKey ? apiKey : '•'.repeat(24)}</span>
          <button class="btn sm" onclick={() => (showKey = !showKey)}><Icon name="eye" /> {showKey ? 'Hide' : 'Show'}</button>
        </div>
        <span class="xsmall muted">
          It is in the downloaded file too (<span class="mono">api: encryption: key:</span>). Not the password for
          VisionState: that one is for the sound and the pictures.
        </span>
        <a class="btn sm" style="align-self:flex-start" href="/config/integrations/dashboard" target="_top">
          Open Devices &amp; services
        </a>
      </div>
    {:else}
      <p class="small muted">
        {devicesError
          ? `Could not ask Home Assistant: ${devicesError}.`
          : 'Waiting for the device: once it is installed and on the Wi-Fi, Home Assistant finds it, and this page shows it.'}
        You can also type its address (in ESPHome's log: <span class="mono">IP Address</span>).
      </p>
    {/if}
    {#if devices.length && !ours}
      <div class="col tight">
        <span class="small muted">Other devices Home Assistant knows the address of:</span>
        <div class="row wrap">
          {#each devices as d (d.entity_id)}
            <button class="btn sm" class:primary={host === d.address} onclick={() => (host = d.address)}>
              {d.name} <span class="mono xsmall">{d.address}</span>
            </button>
          {/each}
        </div>
      </div>
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
  .key {
    overflow-wrap: anywhere;
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
