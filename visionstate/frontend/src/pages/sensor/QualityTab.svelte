<script lang="ts">
  import { untrack } from 'svelte';
  import { api } from '../../lib/api';
  import { stateInfo, toast, toastError } from '../../lib/app.svelte';
  import Icon from '../../lib/components/Icon.svelte';
  import SuspectsCard from '../../lib/components/SuspectsCard.svelte';
  import { ago, pct } from '../../lib/format';
  import { href, paths } from '../../lib/router.svelte';
  import type { Quality, Sensor } from '../../lib/types';
  import type { SensorTab } from '../../lib/ui';

  let { sensor }: { sensor: Sensor } = $props();

  let quality = $state<Quality | null>(null);

  async function load() {
    try {
      quality = await api.quality(sensor.id);
    } catch (err) {
      toastError(err);
    }
  }

  // Reload whenever a new model version is trained.
  // (`sensor` is a new object on every poll: only a new id or version counts.)
  const version = $derived(`${sensor.id}|${sensor.model?.version}`);
  $effect(() => {
    void version;
    untrack(load);
  });

  async function retrain() {
    try {
      await api.retrain(sensor.id);
      toast('Retraining started');
    } catch (err) {
      toastError(err);
    }
  }

  const ACTION_LINKS: Record<string, { label: string; tab?: SensorTab; review?: true }> = {
    label: { label: 'Label', tab: 'label' },
    upload: { label: 'Upload', tab: 'upload' },
    review: { label: 'Review queue', review: true },
  };

  const tiles = $derived([
    {
      label: 'Accuracy',
      value: pct(quality?.accuracy ?? null, 1),
      sub: quality?.accuracy != null ? 'cross-validated on your samples' : 'needs 2+ samples per state',
    },
    { label: 'Samples', value: String(sensor.counts.labelled), sub: `${sensor.counts.unlabelled} waiting for a label` },
    {
      label: 'Training time',
      value: sensor.model ? `${sensor.model.train_seconds.toFixed(1)} s` : '—',
      sub: sensor.model ? `trained ${ago(sensor.model.trained_at)}` : 'not trained yet',
    },
    { label: 'Model', value: sensor.model ? `v${sensor.model.version}` : '—', sub: sensor.model?.backbone ?? '' },
  ]);

  const maxCount = $derived(
    Math.max(1, ...Object.values(sensor.counts.per_state).map((c) => c.day + c.night)),
  );

  function cellStyle(n: number, rowTotal: number, diagonal: boolean) {
    if (!n) return 'background: var(--c-sunken); color: var(--c-faint)';
    const share = n / Math.max(rowTotal, 1);
    return diagonal
      ? `background: rgba(126, 226, 184, ${(0.15 + share * 0.5).toFixed(2)})`
      : `background: rgba(255, 123, 114, ${Math.min(0.6, 0.25 + share * 2).toFixed(2)})`;
  }
</script>

<div class="grid-4">
  {#each tiles as t (t.label)}
    <div class="card pad col tile">
      <span class="small muted">{t.label}</span>
      <span class="value">{t.value}</span>
      <span class="xsmall faint">{t.sub}</span>
    </div>
  {/each}
</div>

<div class="grid-2">
  <section class="card pad">
    <div class="card-title"><h3>What gets mixed up</h3><span class="xsmall faint">rows = true state · columns = AI guess</span></div>
    {#if quality?.confusion}
      {@const keys = quality.confusion.keys}
      <div class="matrix" style:grid-template-columns="110px repeat({keys.length}, minmax(0, 1fr))">
        <span></span>
        {#each keys as k (k)}<span class="head">{stateInfo(sensor, k).name}</span>{/each}
        {#each quality.confusion.matrix as row, i (i)}
          {@const total = row.reduce((a, b) => a + b, 0)}
          <span class="row small" style="gap:8px"><span class="dot" style:background={stateInfo(sensor, keys[i]).color}></span>{stateInfo(sensor, keys[i]).name}</span>
          {#each row as n, j (j)}
            <span class="cell mono" style={cellStyle(n, total, i === j)}>{n}</span>
          {/each}
        {/each}
      </div>
    {:else}
      <p class="small muted">Available once every state has at least two labelled samples.</p>
    {/if}
  </section>

  <section class="card pad">
    <div class="card-title">
      <h3>Samples per state</h3>
      <span class="row xsmall muted" style="gap:12px"><span class="row" style="gap:6px"><span class="sw day"></span>Day</span><span class="row" style="gap:6px"><span class="sw night"></span>Night</span></span>
    </div>
    <div class="col">
      {#each sensor.states as s (s.key)}
        {@const c = sensor.counts.per_state[s.key] ?? { day: 0, night: 0 }}
        <div class="col" style="gap:6px">
          <div class="row small"><span>{s.name}</span><span class="spacer"></span><span class="mono muted">{c.day} day · {c.night} night</span></div>
          <div class="stack">
            <div style:width="{(c.day / maxCount) * 100}%" style:background={s.color}></div>
            <div style:width="{(c.night / maxCount) * 100}%" style:background={s.color} class="night-bar"></div>
          </div>
        </div>
      {/each}
    </div>
    <p class="xsmall faint" style="margin-top:12px">
      Aim for at least {quality?.targets.min_samples_per_state ?? 20} per state, including some at night.
    </p>
  </section>
</div>

<section class="card pad">
  <div class="card-title">
    <h3>Suggestions</h3>
    <button class="btn sm" onclick={retrain}><Icon name="refresh" size={14} /> Retrain now</button>
  </div>
  <div class="col">
    {#each quality?.tips ?? [] as tip, i (i)}
      {@const link = tip.action ? ACTION_LINKS[tip.action] : null}
      <div class="tip">
        <span class="dot" style:background={tip.level === 'ok' ? 'var(--c-accent)' : 'var(--c-warn)'}></span>
        <span class="col" style="gap:2px;flex-grow:1"><strong>{tip.title}</strong><span class="small muted">{tip.text}</span></span>
        {#if tip.action === 'suspects'}
          <button class="linkish small" onclick={() => document.getElementById('suspects')?.scrollIntoView({ behavior: 'smooth' })}>Show</button>
        {:else if link}
          <a class="small" href={href(link.review ? paths.review() : paths.sensor(sensor.id, link.tab))}>{link.label}</a>
        {/if}
      </div>
    {:else}
      <p class="small muted">No suggestions right now.</p>
    {/each}
  </div>
</section>

{#if quality}
  <SuspectsCard {sensor} suspects={quality.suspects} onchange={load} />
{/if}

<style>
  .tile {
    gap: 4px;
  }
  .linkish {
    background: none;
    border: none;
    padding: 0;
    color: var(--c-accent);
    font: inherit;
    cursor: pointer;
  }
  .value {
    font: 600 var(--fs-3xl) var(--font-display);
    letter-spacing: -0.02em;
  }
  .matrix {
    display: grid;
    gap: 6px;
    align-items: center;
  }
  .head {
    text-align: center;
    font-size: var(--fs-sm);
    color: var(--c-faint);
  }
  .cell {
    height: 48px;
    border-radius: var(--radius-sm);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: var(--fs-lg);
  }
  .stack {
    display: flex;
    gap: 2px;
    height: 14px;
  }
  .stack div {
    border-radius: 3px;
  }
  .night-bar {
    opacity: 0.4;
  }
  .sw {
    width: 10px;
    height: 10px;
    border-radius: 3px;
    background: var(--c-muted);
  }
  .sw.night {
    opacity: 0.4;
  }
  .tip {
    display: flex;
    align-items: flex-start;
    gap: var(--space-3);
    padding: var(--space-3);
    border-radius: var(--radius-md);
    background: var(--c-sunken);
  }
  .tip .dot {
    margin-top: 6px;
  }
</style>
