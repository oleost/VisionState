<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '../lib/api';
  import { app, toastError } from '../lib/app.svelte';
  import Icon from '../lib/components/Icon.svelte';
  import { pollWhileVisible } from '../lib/poll';
  import { href, paths } from '../lib/router.svelte';
  import type { SpeedTestInfo, SpeedTestResult, SpeedTestRun } from '../lib/types';
  import { SPEED_TEST_POLL_MS, SPEED_TEST_ROLES } from '../lib/ui';

  let info = $state<SpeedTestInfo | null>(null);
  let run = $state<SpeedTestRun | null>(null);
  let sensorId = $state<number | null>(null);
  let picked = $state<Record<string, boolean>>({});
  let keep = $state(false);
  let starting = $state(false);

  const running = $derived(run?.state === 'running');
  const sensor = $derived(info?.sensors.find((s) => s.id === sensorId) ?? null);
  const models = $derived(sensor?.models.filter((m) => m.available) ?? []);
  const key = (m: { role: string; id: string }) => `${m.role}/${m.id}`;
  const roles = $derived([...new Set(models.map((m) => m.role))]);
  const chosen = $derived(models.filter((m) => picked[key(m)]));
  const toDownload = $derived(chosen.reduce((sum, m) => sum + m.download_size, 0));
  const sizeText = (bytes: number) =>
    bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1e6))} MB`;
  const ms = (value: number | undefined) =>
    value === undefined ? '—' : value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${Math.round(value)} ms`;

  const done = $derived((run?.results ?? []).filter((r) => r.status === 'done' && r.total));
  const resultRoles = $derived([...new Set((run?.results ?? []).map((r) => r.role))]);
  // Models are only compared with those that can take their place: a face model is no detector.
  const peers = (r: SpeedTestResult) => done.filter((o) => o.role === r.role);
  const barShare = (r: SpeedTestResult) => (shown(r)! / Math.max(1, ...peers(r).map((o) => shown(o)!))) * 100;
  const isFastest = (r: SpeedTestResult) =>
    peers(r).length > 1 && Math.min(...peers(r).map((o) => shown(o)!)) === shown(r);
  const isLightest = (r: SpeedTestResult) => {
    const measured = peers(r).filter((o) => o.memory_peak_mb !== undefined);
    return measured.length > 1 && Math.min(...measured.map((o) => o.memory_peak_mb!)) === r.memory_peak_mb;
  };
  /** A face model runs for each person seen, on top of the check: its own time is what counts. */
  const perPerson = (r: SpeedTestResult) => r.role === 'face_models';
  const shown = (r: SpeedTestResult) => (perPerson(r) ? r.ai?.median : r.total?.median);

  /** How much of the time between two checks a model takes. */
  function keepsUp(r: SpeedTestResult): string {
    if (!run || !r.total) return '';
    if (perPerson(r)) return 'This comes on top of the object detector, for each person seen.';
    if (run.sensor.kind === 'sound') {
      const share = r.total.p95 / 1000 / (r.hop_s ?? 1);
      return share < 1
        ? `Takes ${Math.round(share * 100)} % of the time it has for each piece of sound.`
        : 'Too slow to keep up with the sound while it is loud.';
    }
    const seconds = r.total.p95 / 1000;
    return seconds < run.sensor.interval_s
      ? `Fits the sensor's check every ${run.sensor.interval_s} s.`
      : `Slower than the sensor's check every ${run.sensor.interval_s} s: it can not keep up.`;
  }

  function selectSensor(id: number | null) {
    sensorId = id;
    picked = {};
    const current = info?.sensors.find((s) => s.id === id)?.models.find((m) => m.current);
    if (current) picked[key(current)] = true;
  }

  function selectAll(on: boolean) {
    picked = on ? Object.fromEntries(models.map((m) => [key(m), true])) : {};
  }

  async function load() {
    try {
      info = await api.speedTest();
      run = info.run;
      if (sensorId === null && info.sensors.length) selectSensor(run?.sensor.id ?? info.sensors[0].id);
    } catch (err) {
      toastError(err);
    }
  }

  async function poll() {
    if (!running) return;
    try {
      run = (await api.speedTestRun()).run;
      if (!running) await load(); // what is on disk may have changed
    } catch (err) {
      toastError(err);
    }
  }

  async function start() {
    if (sensorId === null || !chosen.length) return;
    starting = true;
    try {
      run = (await api.startSpeedTest(sensorId, chosen.map((m) => ({ role: m.role, id: m.id })), keep)).run;
    } catch (err) {
      toastError(err);
    } finally {
      starting = false;
    }
  }

  async function cancel() {
    try {
      run = (await api.cancelSpeedTest()).run;
    } catch (err) {
      toastError(err);
    }
  }

  onMount(() => {
    void load();
    return pollWhileVisible(SPEED_TEST_POLL_MS, () => void poll());
  });
</script>

<div class="page">
  <p class="small"><a href={href(paths.settings())}>Settings</a> / Speed test</p>
  <h2>Speed test</h2>
  <p class="muted small">
    How fast your AI models are on this machine, and how much memory they take, with a sensor's own camera or microphone.
  </p>

  <div class="col test" data-testid="speed-test">
    {#if run && running}
      <section class="card pad col" data-testid="speed-test-progress">
        <h3>Testing model {run.index || 1} of {run.total}</h3>
        <div class="notice warn">All sensors are paused until the test ends. They keep their last values in Home Assistant.</div>
        <p>{run.step}{run.rounds && run.round ? `: round ${run.round} of ${run.rounds}` : ''}{run.downloaded_mb ? ` (${run.downloaded_mb} MB)` : ''}</p>
        <progress max={run.total} value={Math.max(0, run.index - 1)}></progress>
        <button class="btn" style="align-self:flex-start" onclick={cancel}>Stop the test</button>
      </section>
    {:else if info}
      <section class="card pad col">
        <h3>What to test</h3>
        {#if !info.sensors.length}
          <p class="muted">Make a sensor first: the test uses its camera or microphone.</p>
        {:else}
          <label class="field">
            <span>Sensor</span>
            <select class="input" value={sensorId} onchange={(e) => selectSensor(Number(e.currentTarget.value))} aria-label="Sensor to test with">
              {#each info.sensors as s (s.id)}<option value={s.id}>{s.name}</option>{/each}
            </select>
          </label>
          {#each roles as role (role)}
            <div class="col tight" data-testid="speed-test-role">
              <span class="small muted">{SPEED_TEST_ROLES[role] ?? role}</span>
              {#each models.filter((m) => m.role === role) as m (key(m))}
                <label class="check model">
                  <input type="checkbox" checked={!!picked[key(m)]} onchange={(e) => (picked[key(m)] = e.currentTarget.checked)} />
                  <span>
                    {m.name}
                    {#if m.current}<span class="chip">In use</span>{/if}
                    {#if !m.installed}<span class="chip warn">Download {sizeText(m.download_size)}</span>{/if}
                  </span>
                </label>
              {/each}
            </div>
          {/each}
          <div class="row wrap">
            <button class="btn sm" onclick={() => selectAll(true)}>Select all</button>
            <button class="btn sm" onclick={() => selectAll(false)}>Select none</button>
          </div>
          {#if toDownload}
            <label class="check">
              <input type="checkbox" bind:checked={keep} />
              <span>Keep the downloaded models ({sizeText(toDownload)}). Otherwise they are deleted after the test.</span>
            </label>
          {/if}
          <div class="notice warn">All sensors are paused while the test runs, and every model is tested alone. It takes up to a few minutes per model.</div>
          <button class="btn primary" style="align-self:flex-start" disabled={!chosen.length || starting} onclick={start}>
            <Icon name="play" /> Test {chosen.length} {chosen.length === 1 ? 'model' : 'models'}
          </button>
        {/if}
      </section>
    {/if}

    {#if run?.results.length && !running}
      <section class="card pad col" data-testid="speed-test-results">
        <div class="row wrap between">
          <h3>Results{run.sensor.name ? `: ${run.sensor.name}` : ''}</h3>
          <a class="btn sm" href={api.speedTestReportUrl} download><Icon name="download" size={14} /> Download report</a>
        </div>
        {#if run.state === 'cancelled'}<div class="notice warn">The test was stopped: the rest was not tested.</div>{/if}
        {#if run.state === 'failed'}<div class="notice danger">The test failed: {run.error}</div>{/if}
        <p class="small muted">
          {run.machine.cpus ?? '?'} CPUs{run.machine.memory_mb ? `, ${Math.round(run.machine.memory_mb / 1000)} GB memory` : ''}{done[0]?.picture ? `; pictures of ${done[0].picture}, ${done[0].picture_kb} kB` : ''}.
          Times are the median of the rounds, with 95 % of them within the second number.
        </p>
        {#each resultRoles as role (role)}
        <h4 class="role">{SPEED_TEST_ROLES[role] ?? role}</h4>
        {#each run.results.filter((x) => x.role === role) as r (key(r))}
          <div class="result" data-testid="speed-test-result">
            <div class="row wrap between">
              <strong>{r.name}</strong>
              <span class="row wrap">
                {#if r.current}<span class="chip">In use</span>{/if}
                {#if isFastest(r)}<span class="chip ok">Fastest</span>{/if}
                {#if isLightest(r)}<span class="chip ok">Least memory</span>{/if}
              </span>
            </div>
            {#if r.total}
              <div class="bar"><span style:width="{Math.max(2, barShare(r))}%"></span></div>
              <div class="numbers small">
                {#if perPerson(r)}
                  <span><span class="muted">Each person</span> <b>{ms(r.ai?.median)}</b> <span class="muted">/ {ms(r.ai?.p95)}</span></span>
                {:else}
                  <span><span class="muted">{run.sensor.kind === 'sound' ? 'Each analysis' : 'Whole check'}</span> <b>{ms(r.total.median)}</b> <span class="muted">/ {ms(r.total.p95)}</span></span>
                {/if}
                {#if r.camera && !perPerson(r)}<span><span class="muted">Camera</span> {ms(r.camera.median)}</span>{/if}
                {#if r.sound_ready_ms !== undefined}<span><span class="muted">Stream open with sound after</span> {ms(r.sound_ready_ms)}</span>{/if}
                {#if r.decode && !perPerson(r)}<span><span class="muted">Decode</span> {ms(r.decode.median)}</span>{/if}
                {#if !perPerson(r) && run.sensor.kind !== 'sound'}<span><span class="muted">AI</span> {ms(r.ai?.median)}</span>{/if}
                {#if r.memory_peak_mb !== undefined}
                  <span><span class="muted">Memory</span> {r.memory_peak_mb} MB <span class="muted">peak, {r.memory_loaded_mb} MB loaded</span></span>
                {:else}
                  <span class="muted">Memory is only measured on Home Assistant (Linux)</span>
                {/if}
                <span><span class="muted">Load</span> {ms(r.load_ms)}</span>
              </div>
              <p class="small muted">{keepsUp(r)}</p>
            {:else if r.status === 'skipped'}
              <p class="small muted">Not tested: {r.error}</p>
            {:else if r.status === 'failed'}
              <p class="small danger">Failed: {r.error}</p>
            {:else}
              <p class="small muted">Not finished.</p>
            {/if}
          </div>
        {/each}
        {/each}
        {#if run.deleted.length}<p class="small muted">Downloaded for the test and deleted again: {run.deleted.join(', ')}.</p>{/if}
        <p class="small muted">To use another model, choose it under <a href={href(paths.settings())}>AI model</a>.</p>
      </section>
    {/if}
  </div>
</div>

<style>
  .test {
    max-width: 760px;
    margin-top: 16px;
  }
  .tight {
    gap: 4px;
  }
  .role {
    margin: 8px 0 0;
    font: inherit;
    color: var(--c-muted, inherit);
  }
  .between {
    justify-content: space-between;
  }
  progress {
    width: 100%;
  }
  .result {
    border-top: 1px solid var(--c-border);
    padding-top: 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .bar {
    height: 6px;
    border-radius: 3px;
    background: var(--c-surface-2);
    overflow: hidden;
  }
  .bar span {
    display: block;
    height: 100%;
    background: var(--c-accent);
  }
  .numbers {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 16px;
  }
  .danger {
    color: var(--c-danger);
  }
</style>
