<script lang="ts">
  // Reading sensors: the published value, the last read and exactly what the reader saw.
  import { api } from '../../lib/api';
  import { toastError } from '../../lib/app.svelte';
  import AnalysedFrame from '../../lib/components/AnalysedFrame.svelte';
  import Icon from '../../lib/components/Icon.svelte';
  import { ago, pct } from '../../lib/format';
  import { READING_MODE_INFO, TRIGGER_SOURCES } from '../../lib/ui';
  import { REJECT_REASONS, readingText, readingUnit } from '../../lib/reading';
  import { href, paths } from '../../lib/router.svelte';
  import type { Sensor } from '../../lib/types';

  let { sensor, onchange }: { sensor: Sensor; onchange: () => void } = $props();

  let checking = $state(false);
  const reading = $derived(sensor.reading!);
  const last = $derived(reading.last);

  async function readNow() {
    checking = true;
    try {
      await api.classify(sensor.id);
      setTimeout(onchange, 800);
    } catch (err) {
      toastError(err);
    } finally {
      setTimeout(() => (checking = false), 800);
    }
  }
</script>

<div class="layout">
  <div class="col main">
    <section class="card frame">
      <AnalysedFrame {sensor} />
      <div class="row wrap bar">
        <span class="small">
          {#if sensor.live.error}
            <span class="danger-text">{sensor.live.error}</span>
          {:else if !last}
            <span class="muted">Waiting for the first reading…</span>
          {:else}
            Read <span class="mono">“{last.text || '—'}”</span> · {pct(last.score)} sure
            <span class="faint"> · {ago(last.at)}</span>
          {/if}
        </span>
        <span class="spacer"></span>
        <button class="btn sm" disabled={checking || !sensor.enabled} onclick={readNow}>
          <Icon name="refresh" size={14} />
          {checking ? 'Reading…' : 'Read now'}
        </button>
      </div>
    </section>

    {#if reading.has_image && last}
      <section class="card pad col">
        <div class="card-title"><h3>What the reader sees</h3><span class="xsmall faint">the region after display processing</span></div>
        <img class="seen" src={api.readingImageUrl(sensor.id, last.at)} alt="The region as the number reader saw it" />
        <p class="xsmall muted">
          {#if reading.display === 'counter'}
            One wheel per field, without the dividers between them. If a digit is cut off or sits in two fields, adjust
            the region or the number of digits on the Settings tab.
          {:else}
            If digits are cut off or faint segments show up here, draw the region tighter around the number or pick the
            display type on the Settings tab.
          {/if}
        </p>
      </section>
    {/if}
  </div>

  <aside class="col">
    <section class="card pad col value-card">
      <span class="eyebrow">{READING_MODE_INFO[reading.mode].title}</span>
      <span class="value mono">{readingText(sensor)}</span>
      {#if last?.reason}
        <span class="chip warn">Last reading rejected: {REJECT_REASONS[last.reason] ?? last.reason}</span>
        <span class="xsmall muted">Read “{last.text || '—'}”{last.value ? ` (${last.value} ${readingUnit(reading)})` : ''}; the value above stays.</span>
      {:else if last?.settling}
        <span class="chip info">Last wheel turning</span>
        <span class="xsmall muted">Read “{last.text || '—'}”, one step below; the value above stays until the counter gets there.</span>
      {:else if last}
        <span class="chip ok">Last reading accepted</span>
      {/if}
      <div class="trigger">
        {#if sensor.live.in_burst}<span class="chip info">Reading every {sensor.triggers.burst_interval_s} s</span>{/if}
        {#if sensor.live.last_trigger}
          <span class="xsmall muted">
            {TRIGGER_SOURCES[sensor.live.last_trigger.source] ?? 'Triggered'} {ago(sensor.live.last_trigger.at)}:
            <span class="mono">{sensor.live.last_trigger.detail}</span>
          </span>
        {:else}
          <span class="xsmall faint">
            Reads every {sensor.interval_s} s. <a href={href(paths.sensor(sensor.id, 'settings'))}>Change when it reads</a>.
          </span>
        {/if}
      </div>
    </section>

    <section class="card pad">
      <div class="card-title"><h3>In Home Assistant</h3></div>
      <ul class="entities">
        {#each sensor.entity_ids as id (id)}<li class="mono xsmall">{id}</li>{/each}
      </ul>
    </section>
  </aside>
</div>

<style>
  .layout {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 340px;
    gap: var(--space-6);
    align-items: start;
  }
  .main,
  aside {
    gap: var(--space-4);
  }
  .frame {
    overflow: hidden;
  }
  .bar {
    padding: 14px 18px;
    gap: var(--space-3);
  }
  .seen {
    max-width: 100%;
    max-height: 140px;
    align-self: flex-start;
    border-radius: var(--radius-sm);
    border: 1px solid var(--c-border);
  }
  .value-card {
    gap: var(--space-2);
    align-items: flex-start;
  }
  .eyebrow {
    font-size: var(--fs-xs);
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--c-faint);
  }
  .value {
    font-size: 34px;
    font-weight: 600;
    line-height: 1.1;
    overflow-wrap: anywhere;
  }
  .entities {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
    overflow-wrap: anywhere;
  }
  .trigger {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 6px;
    margin-top: var(--space-2);
    padding-top: var(--space-3);
    border-top: 1px solid var(--c-border);
    align-self: stretch;
  }
  .danger-text {
    color: var(--c-danger);
  }
  @media (max-width: 1000px) {
    .layout {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
