<script lang="ts">
  // Selectable grid of samples with a bulk action bar (label / accept suggestions / delete).
  import { api } from '../api';
  import { stateInfo, toast, toastError } from '../app.svelte';
  import type { SampleItem, Sensor } from '../types';
  import { SAMPLE_ORIGINS } from '../ui';
  import Icon from './Icon.svelte';

  let {
    sensor,
    items,
    onchange,
    showSuggestions = false,
  }: { sensor: Sensor; items: SampleItem[]; onchange: () => void; showSuggestions?: boolean } = $props();

  let selected = $state(new Set<number>());
  let busy = $state(false);

  const count = $derived(items.filter((i) => selected.has(i.id)).length);
  const withSuggestion = $derived(items.filter((i) => !i.labels.length && i.suggestion));

  function toggle(id: number) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    selected = next;
  }

  const selectAll = () => (selected = new Set(items.map((i) => i.id)));
  const clear = () => (selected = new Set());

  async function run(action: () => Promise<unknown>, message: string) {
    busy = true;
    try {
      await action();
      toast(message);
      clear();
      onchange();
    } catch (err) {
      toastError(err);
    } finally {
      busy = false;
    }
  }

  const ids = () => items.filter((i) => selected.has(i.id)).map((i) => i.id);
  const label = (key: string) =>
    run(() => api.labelSamples(sensor.id, ids(), key), `${count} labelled as ${stateInfo(sensor, key).name}`);
  const unlabel = () => run(() => api.labelSamples(sensor.id, ids(), null), `${count} moved to unlabelled`);
  const remove = () => run(() => api.deleteSamples(sensor.id, ids()), `${count} deleted`);
  const acceptAll = () =>
    run(
      () => api.acceptSuggestions(sensor.id, withSuggestion.map((i) => i.id)),
      `${withSuggestion.length} suggestions accepted`,
    );

  function onkey(e: KeyboardEvent) {
    if (!count || busy || (e.target as HTMLElement).closest('input, select, textarea')) return;
    const index = Number(e.key) - 1;
    if (index >= 0 && index < sensor.states.length) label(sensor.states[index].key);
  }
</script>

<svelte:window onkeydown={onkey} />

<div class="col">
  <div class="row wrap">
    <span class="small muted">{items.length} images</span>
    <span class="spacer"></span>
    <button class="btn sm" onclick={selectAll} disabled={!items.length}>Select all</button>
    {#if count}<button class="btn sm ghost" onclick={clear}>Clear selection</button>{/if}
    {#if showSuggestions && withSuggestion.length}
      <button class="btn sm accent-outline" onclick={acceptAll} disabled={busy}>
        <Icon name="check" size={14} /> Accept all {withSuggestion.length} suggestions
      </button>
    {/if}
  </div>

  <div class="grid">
    {#each items as item (item.id)}
      {@const labelled = item.labels.length ? stateInfo(sensor, item.labels[0]) : null}
      {@const sugg = item.suggestion ? stateInfo(sensor, item.suggestion.key) : null}
      {@const isSel = selected.has(item.id)}
      <button
        class="tile"
        class:sel={isSel}
        style:border-color={isSel ? 'var(--c-accent)' : (labelled?.color ?? 'var(--c-border)')}
        aria-pressed={isSel}
        onclick={() => toggle(item.id)}
        title="{SAMPLE_ORIGINS[item.origin] ?? item.origin}{item.is_night ? ' · night' : ''}"
      >
        <img src={api.sampleImageUrl(item.id)} alt="" loading="lazy" />
        <span class="box">{#if isSel}<Icon name="check" size={11} strokeWidth={3} />{/if}</span>
        {#if item.is_night}<span class="ir">IR</span>{/if}
        <span class="chip-wrap">
          {#if labelled}
            <span class="tag solid" style:background={labelled.color}>✓ {labelled.name}</span>
          {:else if sugg && item.suggestion}
            <span class="tag" class:low={item.suggestion.confidence < sensor.threshold}
              >{sugg.name} · {Math.round(item.suggestion.confidence * 100)}%</span
            >
          {:else}
            <span class="tag">Unlabelled</span>
          {/if}
        </span>
      </button>
    {/each}
  </div>

  <div class="actions" class:idle={!count}>
    <strong>{count} selected</strong>
    <span class="small muted">Label as</span>
    {#each sensor.states as s, i (s.key)}
      <button class="btn state" style:border-left-color={s.color} disabled={!count || busy} onclick={() => label(s.key)}>
        <span class="kbd kbd-only">{i + 1}</span>{s.name}
      </button>
    {/each}
    <span class="spacer"></span>
    <button class="btn ghost sm" disabled={!count || busy} onclick={unlabel}>Unlabel</button>
    <button class="btn danger sm" disabled={!count || busy} onclick={remove}><Icon name="trash" size={14} /> Delete</button>
  </div>
</div>

<style>
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
    gap: var(--space-3);
  }
  .tile {
    position: relative;
    padding: 0;
    border: 2px solid var(--c-border);
    border-radius: var(--radius-md);
    background: var(--c-surface);
    overflow: hidden;
    cursor: pointer;
    aspect-ratio: 16 / 10;
  }
  .tile img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
  .box {
    position: absolute;
    left: 6px;
    top: 6px;
    width: 18px;
    height: 18px;
    border-radius: 5px;
    border: 2px solid rgba(255, 255, 255, 0.75);
    background: rgba(0, 0, 0, 0.35);
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--c-accent-ink);
  }
  .sel .box {
    background: var(--c-accent);
    border-color: var(--c-accent);
  }
  .ir {
    position: absolute;
    right: 6px;
    top: 6px;
    font: 500 10px var(--font-mono);
    background: #d7dbe0;
    color: #0f1216;
    padding: 1px 5px;
    border-radius: 4px;
  }
  .chip-wrap {
    position: absolute;
    left: 6px;
    bottom: 6px;
    right: 6px;
    display: flex;
  }
  .tag {
    height: 22px;
    padding: 0 8px;
    border-radius: var(--radius-sm);
    display: inline-flex;
    align-items: center;
    font-size: var(--fs-xs);
    font-weight: 600;
    background: var(--c-overlay);
    color: var(--c-text);
    white-space: nowrap;
    overflow: hidden;
  }
  .tag.solid {
    color: #12151a;
  }
  .tag.low {
    color: var(--c-warn);
  }
  .actions {
    position: sticky;
    bottom: var(--space-4);
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-3) var(--space-4);
    border-radius: var(--radius-lg);
    background: var(--c-surface);
    border: 1px solid var(--c-border-strong);
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
  }
  .actions.idle {
    position: static; /* only follow the scroll while something is selected */
    opacity: 0.6;
  }
  .state {
    border-left-width: 4px;
  }
</style>
