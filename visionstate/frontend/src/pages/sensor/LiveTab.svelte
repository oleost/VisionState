<script lang="ts">
  // Object sensors: the last analysed frame with its boxes, and what each class reports now.
  import { api } from '../../lib/api';
  import { toastError } from '../../lib/app.svelte';
  import EntitiesCard from '../../lib/components/EntitiesCard.svelte';
  import AnalysedFrame from '../../lib/components/AnalysedFrame.svelte';
  import Icon from '../../lib/components/Icon.svelte';
  import { ago, pct, plural } from '../../lib/format';
  import FaceStats from '../../lib/components/FaceStats.svelte';
  import { faceNote, facesOn, objectColor, objectCount, objectKeys, objectName, plateNote, platesOn } from '../../lib/objects';
  import { href, paths } from '../../lib/router.svelte';
  import type { Detection, ObjectLive, Sensor } from '../../lib/types';
  import { IDENTIFIED_BY, TRIGGER_SOURCES } from '../../lib/ui';

  let { sensor, onchange }: { sensor: Sensor; onchange: () => void } = $props();

  let checking = $state(false);

  const objects = $derived(sensor.objects!);
  const keys = $derived(objectKeys(sensor));
  const counts = (d: Detection, key: string) => !d.filtered && (d.key === key || d.label === key || d.group === key);
  const seen = $derived(
    keys.map((key) => ({ key, n: objects.detections.filter((d) => counts(d, key)).length })).filter((x) => x.n > 0),
  );
  const filteredAway = $derived(objects.detections.filter((d) => d.filtered).length);
  // Recognising faces: what the face search said about each person box of the last check.
  const faces = $derived(facesOn(sensor));
  const faceNotes = $derived(
    faces ? objects.detections.filter((d) => d.face && !d.filtered).map((d) => faceNote(d, objects.custom)) : [],
  );
  // Reading plates: what was read on each vehicle box of the last check.
  const plateNotes = $derived(
    platesOn(sensor)
      ? objects.detections
          .filter((d) => d.plate && !d.filtered)
          .map((d) => `${objectName(d.key)} · ${plateNote(d, objects.custom)}`)
      : [],
  );
  // Each class, followed by its own labels ("Rex" under "Dog").
  // A look shared by cars (the same model and colour) cannot tell which of them it is: say so.
  const lookOf = (o: ObjectLive) => {
    const sharing = objects.live.filter((y) => y.group === o.key);
    if (o.identified_by !== 'look' || !sharing.length) return '';
    const names = [o, ...sharing].map((y) => objectName(y.key, objects.custom));
    return ` (${names.slice(0, -1).join(', ')} or ${names.at(-1)})`;
  };
  // A car that shares another car's look under that car ("Tesla EJ86876" under "Tesla").
  const rows = $derived(
    objects.live
      .filter((o) => !o.parent)
      .flatMap((o) => [
        o,
        ...objects.live
          .filter((x) => x.parent === o.key && !x.group)
          .flatMap((x) => [x, ...objects.live.filter((y) => y.group === x.key)]),
      ]),
  );

  async function checkNow() {
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
  <section class="card frame">
    <AnalysedFrame {sensor} teach {onchange} />
    <div class="row wrap bar">
      <span class="small">
        {#if sensor.live.error}
          <span class="danger-text">{sensor.live.error}</span>
        {:else if sensor.live.last_run === null}
          <span class="muted">Waiting for the first check…</span>
        {:else if seen.length}
          The AI sees {seen.map((x) => objectCount(x.n, x.key, objects.custom)).join(', ')}
        {:else}
          <span class="muted">Nothing it looks for in the region</span>
        {/if}
        {#if filteredAway}<span class="faint"> · {filteredAway} filtered away</span>{/if}
        {#if sensor.live.last_run}<span class="faint"> · checked {ago(sensor.live.last_run)}</span>{/if}
      </span>
      <span class="spacer"></span>
      <button class="btn sm" disabled={checking || !sensor.enabled} onclick={checkNow}>
        <Icon name="refresh" size={14} />
        {checking ? 'Checking…' : 'Check now'}
      </button>
    </div>
  </section>

  <aside class="col">
    <section class="card pad">
      <div class="card-title"><h3>Right now</h3><span class="xsmall faint">{ago(sensor.live.last_run)}</span></div>
      <ul class="classes">
        {#each rows as o (o.key)}
          <li class="row" class:own={!!o.parent} class:shared={!!o.group}>
            <span class="dot" style:background={o.on ? objectColor(keys, o.key) : 'var(--c-unknown)'}></span>
            <span class="col" style="gap:2px;min-width:0">
              <span class="row wrap" style="gap:6px">
                <strong class:muted={!o.on}>{objectName(o.key, objects.custom)}</strong>
                {#if objects.use_taught && objects.taught_keys.includes(o.key)}<span class="taught xsmall">taught</span>{/if}
              </span>
              <span class="xsmall faint">
                {#if o.on}Detected{o.identified_by ? ` ${IDENTIFIED_BY[o.identified_by]}` : ''}{lookOf(o)}{o.score
                    ? ` · ${pct(o.score)} sure`
                    : ''}{:else if o.last_seen}Last seen {ago(o.last_seen)}{:else}Not seen yet{/if}
              </span>
            </span>
            <span class="spacer"></span>
            <span class="count mono" class:on={o.on}>{o.on ? o.count : 0}</span>
          </li>
        {/each}
      </ul>
      {#if faces}
        <div class="faces">
          {#each faceNotes as note, i (i)}<span class="xsmall muted">Person · {note}</span>{/each}
          <FaceStats {objects} />
        </div>
      {/if}
      {#if plateNotes.length}
        <div class="faces" data-testid="plate-notes">
          {#each plateNotes as note, i (i)}<span class="xsmall muted">{note}</span>{/each}
        </div>
      {/if}
      <p class="xsmall faint" style="margin-top:12px">
        An object is reported after {plural(sensor.debounce, 'check')} with it and cleared {objects.clear_after_s} s after it
        was last seen. Boxes below {pct(sensor.threshold)} are ignored.
        {#if objects.taught && objects.use_taught}
          Boxes are also compared with <a href={href(paths.sensor(sensor.id, 'dataset'))}>what you taught</a>.
        {/if}
      </p>
      <div class="trigger">
        {#if sensor.live.in_burst}<span class="chip info">Checking every {sensor.live.burst_interval_s ?? sensor.triggers.burst_interval_s} s</span>{/if}
        {#if sensor.live.last_trigger}
          <span class="xsmall muted">
            {TRIGGER_SOURCES[sensor.live.last_trigger.source] ?? 'Triggered'} {ago(sensor.live.last_trigger.at)}:
            <span class="mono">{sensor.live.last_trigger.detail}</span>
          </span>
        {:else}
          <span class="xsmall faint">
            {#if sensor.triggers.regular}
              Checks every {sensor.interval_s} s. <a href={href(paths.sensorSettings(sensor.id, 'checks'))}>Add a motion or change trigger</a>
              to react faster.
            {:else}
              Only checks when triggered. <a href={href(paths.sensorSettings(sensor.id, 'checks'))}>Change when it checks</a>.
            {/if}
          </span>
        {/if}
      </div>
    </section>

    <EntitiesCard {sensor} />
  </aside>
</div>

<style>
  .layout {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 340px;
    gap: var(--space-6);
    align-items: start;
  }
  .frame {
    overflow: hidden;
  }
  .bar {
    padding: 14px 18px;
    gap: var(--space-3);
  }
  aside {
    gap: var(--space-4);
  }
  .classes {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .count {
    min-width: 32px;
    height: 28px;
    padding: 0 8px;
    border-radius: var(--radius-pill);
    background: var(--c-surface-3);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: var(--c-muted);
  }
  .count.on {
    background: var(--c-accent);
    color: var(--c-accent-ink);
    font-weight: 600;
  }
  .own {
    padding-left: var(--space-5); /* an own label sits under its object */
  }
  .own.shared {
    padding-left: calc(2 * var(--space-5)); /* a car under the car whose look it shares */
  }
  .taught {
    padding: 0 6px;
    border-radius: var(--radius-pill);
    border: 1px solid var(--c-border-strong);
    color: var(--c-muted);
  }
  .faces {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin-top: var(--space-3);
    padding-top: var(--space-3);
    border-top: 1px solid var(--c-border);
  }
  .trigger {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 6px;
    margin-top: var(--space-3);
    padding-top: var(--space-3);
    border-top: 1px solid var(--c-border);
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
