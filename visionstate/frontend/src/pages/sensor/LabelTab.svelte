<script lang="ts">
  import { api } from '../../lib/api';
  import { app, stateInfo, toast, toastError } from '../../lib/app.svelte';
  import Icon from '../../lib/components/Icon.svelte';
  import LiveFrame from '../../lib/components/LiveFrame.svelte';
  import ProbBars from '../../lib/components/ProbBars.svelte';
  import { ago, pct } from '../../lib/format';
  import { href, paths } from '../../lib/router.svelte';
  import type { SampleItem, Sensor } from '../../lib/types';

  let { sensor, onchange }: { sensor: Sensor; onchange: () => void } = $props();

  let frameId = $state<string | null>(null);
  let frozen = $state(false);
  let busy = $state(false);
  let recent = $state<SampleItem[]>([]);
  let recentVersion = $state(0);

  const top = $derived(stateInfo(sensor, sensor.live.top));
  const nightTarget = $derived(app.config?.quality.min_night_samples ?? 5);
  const anyNight = $derived(Object.values(sensor.counts.per_state).some((c) => c.night > 0));
  const nightGaps = $derived(
    anyNight ? sensor.states.filter((s) => (sensor.counts.per_state[s.key]?.night ?? 0) < nightTarget) : [],
  );

  $effect(() => {
    void recentVersion;
    api
      .samples(sensor.id, { filter: 'labelled', limit: 4 })
      .then((r) => (recent = r.items))
      .catch(() => {});
  });

  async function label(key: string) {
    if (busy) return;
    busy = true;
    try {
      const { id } = await api.capture(sensor.id, key, frameId);
      const name = stateInfo(sensor, key).name;
      toast(`Saved as ${name} · retraining`, {
        action: {
          label: 'Undo',
          run: async () => {
            await api.deleteSamples(sensor.id, [id]);
            recentVersion++;
            onchange();
          },
        },
      });
      recentVersion++;
      onchange();
    } catch (err) {
      toastError(err);
    } finally {
      busy = false;
    }
  }

  function onkey(e: KeyboardEvent) {
    if ((e.target as HTMLElement).closest('input, select, textarea') || e.ctrlKey || e.metaKey || e.altKey) return;
    const index = Number(e.key) - 1;
    if (index >= 0 && index < sensor.states.length) {
      e.preventDefault();
      label(sensor.states[index].key);
    } else if (e.key === 'f') {
      frozen = !frozen;
    }
  }
</script>

<svelte:window onkeydown={onkey} />

<div class="layout">
  <div class="col main">
    <section class="card frame">
      <LiveFrame sensorId={sensor.id} roi={sensor.roi} {frozen} bind:frameId />
      <div class="row bar">
        <span class="small">
          {#if !sensor.trained}
            <span class="muted">Not trained yet — label a few frames of each state.</span>
          {:else if sensor.live.top}
            The AI thinks <strong style:color={top.color}>{top.name}</strong> · {pct(sensor.live.confidence)} sure
          {:else}
            <span class="muted">Waiting for the first prediction…</span>
          {/if}
        </span>
        <span class="spacer"></span>
        <button class="btn sm" onclick={() => (frozen = !frozen)} aria-pressed={frozen}>
          <Icon name={frozen ? 'play' : 'pause'} size={14} />
          {frozen ? 'Resume live' : 'Freeze frame'}
        </button>
      </div>
    </section>

    <div class="row">
      <h2>What state is this?</h2>
      <span class="spacer"></span>
      <span class="small muted">Press 1–{sensor.states.length} · F freezes the frame</span>
    </div>
    <div class="buttons" style:grid-template-columns="repeat({Math.min(sensor.states.length, 4)}, minmax(0, 1fr))">
      {#each sensor.states as s, i (s.key)}
        {@const c = sensor.counts.per_state[s.key] ?? { day: 0, night: 0 }}
        <button class="state-btn" style:border-top-color={s.color} disabled={busy} onclick={() => label(s.key)}>
          <span class="row" style="gap:10px"><span class="kbd">{i + 1}</span><span class="name">{s.name}</span></span>
          <span class="small muted">{c.day + c.night} samples</span>
        </button>
      {/each}
    </div>
  </div>

  <aside class="col">
    <section class="card pad">
      <div class="card-title"><h3>Current prediction</h3><span class="xsmall faint">{ago(sensor.live.last_run)}</span></div>
      {#if sensor.trained}
        <ProbBars states={sensor.states} probs={sensor.live.probs} threshold={sensor.threshold} />
        <p class="xsmall faint" style="margin-top:12px">
          Line = {pct(sensor.threshold)} threshold. Below it the sensor reports {app.config?.unknown_state ?? 'unknown'}.
        </p>
      {:else}
        <p class="small muted">Label at least two different states to train the first model.</p>
      {/if}
    </section>

    <section class="card pad">
      <div class="card-title"><h3>Coverage</h3></div>
      <table>
        <thead><tr><th>State</th><th>Day</th><th>Night</th></tr></thead>
        <tbody>
          {#each sensor.states as s (s.key)}
            {@const c = sensor.counts.per_state[s.key] ?? { day: 0, night: 0 }}
            <tr>
              <td><span class="dot" style:background={s.color}></span> {s.name}</td>
              <td class="mono">{c.day}</td>
              <td class="mono" class:warn={anyNight && c.night < nightTarget}>{c.night}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </section>

    {#if nightGaps.length}
      <div class="notice warn">
        <Icon name="alert" />
        <span>{nightGaps.map((s) => s.name).join(', ')} {nightGaps.length === 1 ? 'has' : 'have'} few night images. Label a few after dark so IR frames are recognised.</span>
      </div>
    {/if}

    <section class="card pad">
      <div class="card-title"><h3>Recently labelled</h3><a class="small" href={href(paths.sensor(sensor.id, 'dataset'))}>Dataset</a></div>
      {#if recent.length}
        <div class="recent">
          {#each recent as r (r.id)}
            {@const info = stateInfo(sensor, r.labels[0])}
            <div class="col" style="gap:4px">
              <img src={api.sampleImageUrl(r.id)} alt="" loading="lazy" />
              <span class="row xsmall" style="gap:6px"><span class="dot" style:background={info.color}></span>{info.name} · {ago(r.created_at)}</span>
            </div>
          {/each}
        </div>
      {:else}
        <p class="small muted">Nothing yet.</p>
      {/if}
    </section>
  </aside>
</div>

<style>
  .layout {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 380px;
    gap: var(--space-6);
    align-items: start;
  }
  .main {
    gap: var(--space-4);
  }
  .frame {
    overflow: hidden;
  }
  .bar {
    padding: 14px 18px;
  }
  .buttons {
    display: grid;
    gap: 14px;
  }
  .state-btn {
    min-height: 88px;
    border-radius: var(--radius-lg);
    background: var(--c-surface);
    border: 1px solid var(--c-border-strong);
    border-top: 4px solid;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    justify-content: center;
    gap: 6px;
    padding: 0 18px;
    color: var(--c-text);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .state-btn:hover {
    background: var(--c-surface-2);
  }
  .state-btn:disabled {
    opacity: 0.6;
  }
  .name {
    font: 600 20px var(--font-display);
  }
  aside {
    gap: var(--space-4);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--fs-md);
  }
  th {
    text-align: right;
    font-weight: 400;
    color: var(--c-faint);
    padding-bottom: 6px;
  }
  th:first-child {
    text-align: left;
  }
  td {
    padding: 4px 0;
    text-align: right;
  }
  td:first-child {
    text-align: left;
  }
  td.warn {
    color: var(--c-warn);
  }
  .recent {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px;
  }
  .recent img {
    border-radius: var(--radius-sm);
    aspect-ratio: 16 / 10;
    object-fit: cover;
    width: 100%;
  }
  @media (max-width: 1000px) {
    .layout {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
