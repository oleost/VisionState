<script lang="ts">
  // Reading sensors: how often readings are rejected and why, per day, and what the user verified.
  import { api } from '../../lib/api';
  import { app, refreshStatus, toast, toastError } from '../../lib/app.svelte';
  import ConfirmButton from '../../lib/components/ConfirmButton.svelte';
  import Icon from '../../lib/components/Icon.svelte';
  import ReadingVerdict from '../../lib/components/ReadingVerdict.svelte';
  import RoiEditor from '../../lib/components/RoiEditor.svelte';
  import { dateTime, pct } from '../../lib/format';
  import { REJECT_REASONS, rate, readingDetail as detail, readingRoi, readingUnit } from '../../lib/reading';
  import { href, paths } from '../../lib/router.svelte';
  import type { ReadingQuality, Sensor } from '../../lib/types';

  let { sensor }: { sensor: Sensor } = $props();

  let quality = $state<ReadingQuality | null>(null);
  let open = $state<number | null>(null);
  let hover = $state<number | null>(null);
  // Newer rejected readings than the list shows. The list itself only changes when asked, so a
  // row does not move away while it is being answered (new readings can arrive every few seconds).
  let newer = $state<ReadingQuality['items']>([]);

  async function load(list = true) {
    try {
      const fresh = await api.readingQuality(sensor.id);
      if (list || !quality) {
        quality = fresh;
        newer = [];
      } else {
        const shown = new Set(quality.items.map((p) => p.id));
        newer = fresh.items.filter((p) => !shown.has(p.id));
        quality = { ...fresh, items: quality.items };
      }
    } catch (err) {
      toastError(err);
    }
  }

  // Figures follow every new reading; the list waits for "Show new readings".
  const refreshKey = $derived(`${sensor.reading?.value}|${sensor.reading?.last?.at}`);
  let loaded = false;
  $effect(() => {
    void refreshKey;
    load(!loaded);
    loaded = true;
  });

  const unit = $derived(sensor.reading ? readingUnit(sensor.reading) : '');
  const PERIOD_LABEL: Record<number, string> = { 1: 'Today', 7: 'Last 7 days', 30: 'Last 30 days' };
  const longest = $derived(quality?.periods[quality.periods.length - 1] ?? null);
  const reasons = $derived(
    Object.entries(longest?.by_reason ?? {}).sort((a, b) => b[1] - a[1]),
  );
  const maxReads = $derived(Math.max(1, ...(quality?.daily.map((d) => d.reads) ?? [0])));
  const shownDay = $derived(quality ? quality.daily[hover ?? quality.daily.length - 1] : null);
  const v = $derived(quality?.verified);
  const verifiedTotal = $derived(v ? v.misread_rejected + v.right_rejected + v.misread_accepted + v.right_accepted : 0);
  const unchecked = $derived(v?.unchecked_accepted ?? 0);

  let meter = $state(''); // the export's optional "What meter is this?"

  let dismissing = $state(false);
  async function dismissAll() {
    dismissing = true;
    try {
      const { dismissed } = await api.dismissReview(sensor.id);
      toast(`Dismissed ${dismissed} item${dismissed === 1 ? '' : 's'} from the review queue`);
      refreshStatus();
      await load();
    } catch (err) {
      toastError(err);
    } finally {
      dismissing = false;
    }
  }

  const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);
  const dayName = (day: string) =>
    new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
</script>

{#if quality === null}
  <p class="muted">Loading…</p>
{:else}
  <div class="grid-4">
    {#each quality.periods as p (p.days)}
      <div class="card pad col tile">
        <span class="small muted">{PERIOD_LABEL[p.days] ?? `${p.days} days`}</span>
        <!-- Accepted, not "correct": a misread can pass every check (see Misreads found). -->
        <span class="value">{rate(p.accepted, p.reads)}</span>
        <span class="xsmall faint">
          {p.reads ? `${p.accepted} of ${p.reads} readings accepted` : 'no readings'}
        </span>
      </div>
    {/each}
    <div class="card pad col tile">
      <span class="small muted">Misreads found</span>
      <span class="value">{v ? v.misread_rejected + v.misread_accepted : 0}</span>
      <span class="xsmall faint">
        {#if v && v.waiting}
          {v.waiting} waiting in the <a href={href(paths.review())}>review queue</a>
        {:else}
          {verifiedTotal} reading{verifiedTotal === 1 ? '' : 's'} verified
        {/if}
      </span>
    </div>
  </div>

  <div class="two">
    <section class="card pad col">
      <div class="card-title">
        <h3>Readings per day</h3>
        <span class="row legend xsmall muted">
          <span class="row key"><span class="swatch ok"></span>Accepted</span>
          <span class="row key"><span class="swatch warn"></span>Rejected</span>
        </span>
      </div>
      {#if longest?.reads}
        <p class="small readout" aria-live="polite">
          {#if shownDay}
            <strong>{dayName(shownDay.day)}</strong> ·
            {shownDay.reads ? `${shownDay.reads} readings, ${shownDay.rejected} rejected (${rate(shownDay.rejected, shownDay.reads)})` : 'no readings'}
          {/if}
        </p>
        <div class="chart" role="group" aria-label="Readings per day" onpointerleave={() => (hover = null)}>
          {#each quality.daily as d, i (d.day)}
            <button
              type="button"
              class="day"
              class:current={hover === i}
              aria-label="{dayName(d.day)}: {d.reads} readings, {d.rejected} rejected"
              onpointerenter={() => (hover = i)}
              onfocus={() => (hover = i)}
              onclick={() => (hover = i)}
            >
              <span class="bar" style:height="{(d.reads / maxReads) * 100}%">
                {#if d.rejected}<span class="seg warn" style:flex-grow={d.rejected}></span>{/if}
                {#if d.accepted}<span class="seg ok" style:flex-grow={d.accepted}></span>{/if}
              </span>
            </button>
          {/each}
        </div>
        <div class="row axis xsmall faint">
          <span>{dayName(quality.daily[0].day)}</span><span class="spacer"></span><span>Today</span>
        </div>
      {:else}
        <p class="small muted">No readings in the last {longest?.days ?? 30} days yet.</p>
      {/if}
    </section>

    <section class="card pad col">
      <h3>Why readings were rejected</h3>
      <p class="xsmall faint">Last {longest?.days ?? 30} days.</p>
      {#if reasons.length}
        <div class="col reasons">
          {#each reasons as [reason, n] (reason)}
            <div class="col" style="gap:4px">
              <span class="row small"><span>{REJECT_REASONS[reason] ?? reason}</span><span class="spacer"></span><span class="mono">{n}</span></span>
              <span class="track"><span class="fill" style:width="{(n / (longest?.rejected || 1)) * 100}%"></span></span>
            </div>
          {/each}
        </div>
      {:else}
        <p class="small muted">None rejected.</p>
      {/if}
      {#if v && verifiedTotal}
        <div class="col verified small">
          <span><strong>{v.misread_rejected}</strong> {plural(v.misread_rejected, 'misread was', 'misreads were')} caught by the checks.</span>
          {#if v.misread_accepted}
            <span class="danger-text"
              ><strong>{v.misread_accepted}</strong> {plural(v.misread_accepted, 'misread', 'misreads')} passed the checks and
              {plural(v.misread_accepted, 'was', 'were')} published.</span
            >
          {/if}
          {#if v.right_rejected}
            <span
              ><strong>{v.right_rejected}</strong> {plural(v.right_rejected, 'correct reading was', 'correct readings were')} rejected
              — is the change limit too low?</span
            >
          {/if}
          {#if v.right_accepted}
            <span><strong>{v.right_accepted}</strong> accepted {plural(v.right_accepted, 'reading', 'readings')} confirmed right.</span>
          {/if}
        </div>
      {/if}
    </section>
  </div>

  <section class="col list-section">
    <div class="col" style="gap:4px">
      <h3>Rejected and checked readings</h3>
      <p class="small muted">
        Tell whether the reader read the meter right: a misread that was rejected shows the checks work; a right
        reading that was rejected points at a setting. The reader does not learn from your answers — they show how
        reliable the reading is. Spot checks of accepted readings can be switched on under Settings → Sensor output.
      </p>
    </div>
    {#if verifiedTotal || unchecked}
      <div class="card pad col share">
        <div class="col" style="gap:2px;min-width:0">
          <strong class="small">Help improve reading</strong>
          <span class="xsmall muted">
            A ZIP of the readings — only the region — to share in
            <a href="https://github.com/oleost/VisionState/discussions" target="_blank" rel="noopener">GitHub Discussions</a>.
            Shared, the images are public domain (CC0).
          </span>
        </div>
        <label class="field small">
          <span>What meter is this? <span class="hint">optional</span></span>
          <input
            class="input sm"
            bind:value={meter}
            maxlength={app.config?.reading_export_meter_max_chars}
            placeholder="e.g. water meter with red wheels, ESP32 camera with flash"
          />
        </label>
        <div class="row wrap actions">
          <a class="btn sm" href={api.readingExportUrl(sensor.id, meter)} download>
            <Icon name="download" size={14} /> Export readings
          </a>
        </div>
      </div>
    {/if}
    {#if v?.waiting}
      <div class="row wrap dismiss">
        <span class="small muted">{v.waiting} waiting in the review queue.</span>
        <ConfirmButton class="btn sm" disabled={dismissing} confirmLabel="Press again" onconfirm={dismissAll}>
          Dismiss all
        </ConfirmButton>
      </div>
    {/if}
    {#if newer.length}
      <button class="btn sm newer" onclick={() => load()}>
        <Icon name="refresh" size={14} /> Show {newer.length} new reading{newer.length === 1 ? '' : 's'}
      </button>
    {/if}
    {#if !quality.items.length}
      <p class="muted">Nothing yet — rejected readings show up here.</p>
    {/if}
    {#each quality.items as p (p.id)}
      {@const d = detail(p)}
      <div class="card item">
        <div class="row head">
          <button class="row thumb-btn" onclick={() => (open = open === p.id ? null : p.id)} aria-expanded={open === p.id}>
            {#if p.has_frame}<img src={api.historyImageUrl(p.id, 'thumb')} alt="" loading="lazy" />{/if}
            <span class="col" style="gap:4px;min-width:0">
              <span class="row wrap" style="gap:8px">
                <strong class="mono">“{d.text || '—'}”</strong>
                {#if d.value}<span class="mono small muted">→ {d.value}{unit ? ` ${unit}` : ''}</span>{/if}
                {#if d.reason}
                  <span class="chip warn">{REJECT_REASONS[d.reason] ?? d.reason}</span>
                {:else if p.review_reason === 'spot_check'}
                  <span class="chip">Spot check</span>
                {:else}
                  <span class="chip ok">Accepted</span>
                {/if}
              </span>
              <span class="xsmall faint">{dateTime(p.created_at)} · {pct(p.confidence)} sure</span>
            </span>
            <Icon name={open === p.id ? 'chevron-up' : 'chevron-down'} size={16} />
          </button>
          <span class="spacer"></span>
          <ReadingVerdict
            item={p}
            reading={sensor.reading}
            {unit}
            onanswer={(verdict) => {
              Object.assign(p, verdict, { reviewed: true });
              load(false);
            }}
          />
        </div>
        {#if open === p.id && p.has_frame}
          <div class="full"><RoiEditor src={api.historyImageUrl(p.id)} roi={readingRoi(p, sensor)} /></div>
        {/if}
      </div>
    {/each}
  </section>
{/if}

<style>
  .tile {
    gap: 4px;
  }
  .value {
    font: 600 var(--fs-3xl) var(--font-display);
    letter-spacing: -0.02em;
  }
  .two {
    display: grid;
    grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
    gap: var(--space-5);
    margin-top: var(--space-5);
  }
  .legend {
    gap: var(--space-3);
  }
  .key {
    gap: 6px;
  }
  .swatch {
    width: 10px;
    height: 10px;
    border-radius: 3px;
  }
  .swatch.ok,
  .seg.ok {
    background: var(--c-accent);
  }
  .swatch.warn,
  .seg.warn {
    background: var(--c-warn);
  }
  .readout {
    min-height: 1.4em;
    margin: 0;
    color: var(--c-text-2);
  }
  .chart {
    display: flex;
    align-items: flex-end;
    gap: 2px;
    height: 160px;
    border-bottom: 1px solid var(--c-border);
  }
  .day {
    flex: 1 1 0;
    min-width: 0;
    height: 100%;
    display: flex;
    align-items: flex-end;
    padding: 0;
    background: none;
    border: none;
    border-radius: 4px 4px 0 0;
    cursor: pointer;
  }
  .day.current {
    background: var(--c-surface-3);
  }
  .bar {
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-height: 2px;
  }
  .seg {
    flex-basis: 0;
    min-height: 2px;
  }
  .seg:first-child {
    border-radius: 4px 4px 0 0;
  }
  .axis {
    margin-top: 4px;
  }
  .reasons {
    gap: var(--space-3);
  }
  .track {
    height: 6px;
    border-radius: 3px;
    background: var(--c-sunken);
    overflow: hidden;
  }
  .fill {
    display: block;
    height: 100%;
    border-radius: 3px;
    background: var(--c-warn);
  }
  .verified {
    gap: 6px;
    margin-top: var(--space-4);
    padding-top: var(--space-3);
    border-top: 1px solid var(--c-border);
    color: var(--c-text-2);
  }
  .danger-text {
    color: var(--c-danger-text);
  }
  .list-section {
    gap: var(--space-2);
    margin-top: var(--space-6);
  }
  .item {
    padding: var(--space-2);
  }
  .newer {
    align-self: flex-start;
  }
  .dismiss {
    gap: var(--space-3);
  }
  .share {
    gap: var(--space-3);
  }
  .share .field {
    max-width: 520px;
  }
  .share .actions {
    gap: var(--space-2);
    justify-content: flex-end;
  }
  .head {
    gap: var(--space-3);
    flex-wrap: wrap;
  }
  .thumb-btn {
    gap: var(--space-4);
    min-width: 0;
    padding: 0;
    background: transparent;
    border: none;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  img {
    width: 128px;
    aspect-ratio: 16 / 10;
    object-fit: cover;
    border-radius: var(--radius-sm);
    flex-shrink: 0;
  }
  .full {
    margin-top: var(--space-2);
    border-radius: var(--radius-md);
    overflow: hidden;
  }
  @media (max-width: 900px) {
    .two {
      grid-template-columns: minmax(0, 1fr);
    }
  }
  @media (max-width: 600px) {
    img {
      width: 96px;
    }
    .thumb-btn {
      gap: var(--space-3);
    }
  }
</style>
