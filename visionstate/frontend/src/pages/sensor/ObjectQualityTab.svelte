<script lang="ts">
  // Object sensors: what the sensor was taught (boxes per label, own labels) and the boxes it
  // filtered away lately, so it is always visible what teaching changes. Shown once a box was taught.
  import { untrack } from 'svelte';
  import { api } from '../../lib/api';
  import { toast, toastError } from '../../lib/app.svelte';
  import ConfirmButton from '../../lib/components/ConfirmButton.svelte';
  import Icon from '../../lib/components/Icon.svelte';
  import TeachableFrame from '../../lib/components/TeachableFrame.svelte';
  import { ago, dateTime, pct } from '../../lib/format';
  import { noneLabel, objectName } from '../../lib/objects';
  import { href, paths } from '../../lib/router.svelte';
  import type { Sensor, Taught, TaughtBox } from '../../lib/types';

  let { sensor, onchange }: { sensor: Sensor; onchange: () => void } = $props();

  let taught = $state<Taught | null>(null);
  let open = $state<number | null>(null);

  async function load() {
    try {
      taught = await api.taught(sensor.id);
    } catch (err) {
      toastError(err);
    }
  }
  // Reload on every poll of the sensor (a new `sensor` object): it shows what was taught (on Live
  // or in the history) and the boxes recent checks filtered away.
  $effect(() => {
    void sensor;
    untrack(load);
  });

  const own = $derived(sensor.objects?.custom ?? []);
  const compared = $derived((sensor.objects?.taught_keys ?? []).map((k) => objectName(k).toLowerCase()));

  interface Group {
    id: string;
    title: string;
    help: string;
    label: (Taught['labels'][number]) | null;
    boxes: TaughtBox[];
  }

  // One group per answer: "Not a person", "Person", "Rex" (own label) …
  const groups = $derived.by(() => {
    if (!taught) return [] as Group[];
    const result = new Map<string, Group>();
    for (const label of taught.labels) {
      result.set(label.key, {
        id: label.key,
        title: label.name,
        help: label.active
          ? `Your label, a kind of ${objectName(label.parent).toLowerCase()}`
          : `Your label — not in use while ${objectName(label.parent).toLowerCase()} is not one of the sensor's objects`,
        label,
        boxes: [],
      });
    }
    for (const box of taught.examples) {
      const none = box.label === noneLabel();
      const id = none ? `none:${box.detected}` : box.label;
      if (!result.has(id)) {
        result.set(id, {
          id,
          title: none ? `Not a ${objectName(box.detected ?? '').toLowerCase()}` : objectName(box.label, own),
          help: none ? 'Filtered away when a box looks like these' : 'Counted as this when a box looks like these',
          label: null,
          boxes: [],
        });
      }
      result.get(id)!.boxes.push(box);
    }
    return [...result.values()];
  });

  function badge(box: TaughtBox): string {
    if (box.detected === null) return 'missed';
    if (box.label !== noneLabel() && box.label !== box.detected && !own.some((l) => l.key === box.label))
      return `AI said ${objectName(box.detected).toLowerCase()}`;
    return '';
  }

  async function forget(box: TaughtBox) {
    try {
      await api.forgetBox(sensor.id, box.id);
      toast('Forgotten');
      onchange();
      load();
    } catch (err) {
      toastError(err);
    }
  }

  async function removeLabel(key: string, name: string) {
    try {
      await api.removeLabel(sensor.id, key);
      toast(`${name} removed, with its entities in Home Assistant`);
      onchange();
      load();
    } catch (err) {
      toastError(err);
    }
  }
</script>

<div class="layout">
  <div class="col" style="gap:var(--space-5)">
    <section class="card pad">
      <div class="card-title"><h3>What you taught</h3></div>
      {#if taught && !taught.use_taught}
        <div class="notice warn" style="margin-bottom:12px">
          Turned off under <a href={href(paths.sensor(sensor.id, 'settings'))}>Settings</a>: the AI alone decides until you turn it on again.
        </div>
      {/if}
      <p class="small muted">
        {#if compared.length}
          Each {compared.join(', ')} the AI finds is compared with these boxes. When it clearly looks like one of them it
          gets that answer; otherwise the AI's own answer stands.
        {:else}
          Nothing to compare with yet.
        {/if}
        Tap a box on the <a href={href(paths.sensor(sensor.id, 'live'))}>Live</a> tab or in the history to teach more.
      </p>
    </section>

    {#if taught === null}
      <p class="muted">Loading…</p>
    {:else}
      {#each groups as g (g.id)}
        <section class="card pad">
          <div class="card-title">
            <div class="col" style="gap:2px">
              <h3>{g.title} <span class="faint count">{g.boxes.length}</span></h3>
              <span class="xsmall faint">{g.help}</span>
            </div>
            {#if g.label}
              <ConfirmButton class="btn sm danger" onconfirm={() => removeLabel(g.label!.key, g.label!.name)} confirmLabel="Tap again to remove">
                <Icon name="trash" size={14} /> Remove label
              </ConfirmButton>
            {/if}
          </div>
          {#if g.boxes.length}
            <div class="boxes">
              {#each g.boxes as box (box.id)}
                <figure>
                  <img src={api.sampleImageUrl(box.id)} alt={g.title} loading="lazy" />
                  {#if badge(box)}<span class="badge">{badge(box)}</span>{/if}
                  <button class="forget" onclick={() => forget(box)} aria-label="Forget this box" title="Forget this box">
                    <Icon name="x" size={12} />
                  </button>
                  <figcaption class="xsmall faint" title={dateTime(box.created_at)}>{ago(box.created_at)}</figcaption>
                </figure>
              {/each}
            </div>
          {:else}
            <p class="xsmall muted">No boxes taught with this label. Teach one on the Live tab, or remove the label.</p>
          {/if}
        </section>
      {/each}
    {/if}
  </div>

  <section class="card pad filtered">
    <div class="card-title"><h3>Filtered away lately</h3></div>
    <p class="small muted">
      Frames where something the AI found was not counted because of what you taught. Check now and then that
      nothing real is filtered away; tap a box to correct it.
    </p>
    {#if taught?.filtered.length}
      <div class="col" style="gap:var(--space-2);margin-top:12px">
        {#each taught.filtered as p (p.id)}
          <div class="item">
            <button class="row head" onclick={() => (open = open === p.id ? null : p.id)} aria-expanded={open === p.id}>
              {#if p.has_frame}<img src={api.historyImageUrl(p.id, 'thumb')} alt="" loading="lazy" />{/if}
              <span class="col" style="gap:4px;flex-grow:1;min-width:0">
                <strong class="small">{objectName(p.state_key, own)} filtered away <span class="mono muted">{pct(p.confidence)}</span></strong>
                <span class="xsmall faint">{dateTime(p.created_at)}</span>
              </span>
              <Icon name={open === p.id ? 'chevron-up' : 'chevron-down'} size={16} />
            </button>
            {#if open === p.id && p.has_frame}
              <div class="full">
                <TeachableFrame
                  {sensor}
                  src={api.historyImageUrl(p.id)}
                  detections={p.detections ?? []}
                  source={{ history_id: p.id }}
                  onchange={() => {
                    onchange();
                    load();
                  }}
                />
              </div>
            {/if}
          </div>
        {/each}
      </div>
    {:else if taught}
      <p class="xsmall faint" style="margin-top:12px">Nothing filtered away yet.</p>
    {/if}
  </section>
</div>

<style>
  .layout {
    display: grid;
    grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr);
    gap: var(--space-5);
    align-items: start;
  }
  h3 .count {
    font-weight: 400;
    font-size: var(--fs-md);
  }
  p {
    margin: 0;
  }
  .boxes {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
    gap: var(--space-3);
  }
  figure {
    position: relative;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }
  figure img {
    width: 100%;
    aspect-ratio: 1;
    object-fit: contain;
    background: var(--c-sunken);
    border-radius: var(--radius-sm);
  }
  .badge {
    position: absolute;
    left: 4px;
    top: 4px;
    max-width: calc(100% - 40px);
    overflow: hidden;
    text-overflow: ellipsis;
    padding: 1px 6px;
    border-radius: var(--radius-pill);
    background: var(--c-overlay);
    font-size: var(--fs-xs);
    white-space: nowrap;
  }
  .forget {
    position: absolute;
    right: 2px;
    top: 2px;
    width: 30px;
    height: 30px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--radius-pill);
    border: none;
    background: var(--c-overlay);
    color: var(--c-text);
    cursor: pointer;
  }
  .forget:hover {
    color: var(--c-danger-text);
  }
  figcaption {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .item {
    border: 1px solid var(--c-border);
    border-radius: var(--radius-md);
    padding: var(--space-2);
  }
  .head {
    width: 100%;
    gap: var(--space-3);
    padding: 0 var(--space-2) 0 0;
    background: transparent;
    border: none;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .head img {
    width: 96px;
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
  .full :global(.teachbar) {
    border-bottom: none;
  }
  @media (max-width: 1000px) {
    .layout {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
