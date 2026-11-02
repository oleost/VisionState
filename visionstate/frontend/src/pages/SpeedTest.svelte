<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '../lib/api';
  import { app, toast, toastError } from '../lib/app.svelte';
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
  const slowest = $derived(Math.max(1, ...done.map((r) => r.total!.median)));
  const fastest = $derived(done.length ? done.reduce((a, b) => (b.total!.median < a.total!.median ? b : a)) : null);
  const lightest = $derived(
    done.filter((r) => r.memory_peak_mb !== undefined).reduce<SpeedTestResult | null>(
      (a, b) => (!a || b.memory_peak_mb! < a.memory_peak_mb! ? b : a),
      null,
    ),
  );

  /** How much of the time between two checks a model takes. */
  function keepsUp(r: SpeedTestResult): string {
    if (!run || !r.total) return '';
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

  /** Without names or addresses: fit to paste in a post. */
  function report(): string {
    if (!run) return '';
    const m = run.machine;
    const lines = [
      `VisionState ${m.version} speed test, ${m.cpus ?? '?'} CPUs${m.arch ? ` (${m.arch})` : ''}${m.memory_mb ? `, ${Math.round(m.memory_mb / 1000)} GB` : ''}`,
      `${run.sensor.kind} sensor, ${run.sensor.source_type}${done[0]?.picture ? `, ${done[0].picture}, ${done[0].picture_kb} kB` : ''}`,
      '',
      'model | whole check | camera | decode | AI | memory',
    ];
    for (const r of run.results) {
      lines.push(
        r.total
          ? `${r.id} | ${ms(r.total.median)} (p95 ${ms(r.total.p95)}) | ${ms(r.camera?.median)} | ${ms(r.decode?.median)} | ${ms(r.ai?.median)} | ${r.memory_peak_mb ?? '?'} MB`
          : `${r.id} | ${r.status}`,
      );
    }
    return lines.join('\n');
  }

  async function copyReport() {
    try {
      await navigator.clipboard.writeText(report());
      toast('Copied');
    } catch {
      toast('Could not copy: your browser does not allow it here');
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
          <button class="btn sm" onclick={copyReport}>Copy report</button>
        </div>
        {#if run.state === 'cancelled'}<div class="notice warn">The test was stopped: the rest was not tested.</div>{/if}
        {#if run.state === 'failed'}<div class="notice danger">The test failed: {run.error}</div>{/if}
        <p class="small muted">
          {run.machine.cpus ?? '?'} CPUs{run.machine.memory_mb ? `, ${Math.round(run.machine.memory_mb / 1000)} GB memory` : ''}{done[0]?.picture ? `; pictures of ${done[0].picture}, ${done[0].picture_kb} kB` : ''}.
          Times are the median of the rounds, with 95 % of them within the second number.
        </p>
        {#each run.results as r (key(r))}
          <div class="result" data-testid="speed-test-result">
            <div class="row wrap between">
              <strong>{r.name}</strong>
              <span class="row wrap">
                {#if r.current}<span class="chip">In use</span>{/if}
                {#if done.length > 1 && r === fastest}<span class="chip ok">Fastest</span>{/if}
                {#if done.length > 1 && r === lightest}<span class="chip ok">Least memory</span>{/if}
              </span>
            </div>
            {#if r.total}
              <div class="bar" title="Whole check"><span style:width="{Math.max(2, (r.total.median / slowest) * 100)}%"></span></div>
              <div class="numbers small">
                <span><span class="muted">{run.sensor.kind === 'sound' ? 'Each analysis' : 'Whole check'}</span> <b>{ms(r.total.median)}</b> <span class="muted">/ {ms(r.total.p95)}</span></span>
                {#if r.camera}<span><span class="muted">Camera</span> {ms(r.camera.median)}</span>{/if}
                {#if r.sound_ready_ms !== undefined}<span><span class="muted">Stream open with sound after</span> {ms(r.sound_ready_ms)}</span>{/if}
                {#if r.decode}<span><span class="muted">Decode</span> {ms(r.decode.median)}</span>{/if}
                <span><span class="muted">AI</span> {ms(r.ai?.median)}</span>
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
