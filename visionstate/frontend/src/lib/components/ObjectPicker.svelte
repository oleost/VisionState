<script lang="ts">
  // Pick the object classes a sensor looks for: popular ones first, all others behind "Show all".
  // Labels, groups, popular list and the maximum come from the backend config.
  import { app } from '../app.svelte';
  import Icon from './Icon.svelte';

  let { selected = $bindable([]) }: { selected: string[] } = $props();

  let showAll = $state(false);
  let query = $state('');

  const labels = $derived(app.config?.object_labels ?? []);
  const max = $derived(app.config?.object_max_classes ?? 20);
  const byKey = $derived(new Map(labels.map((l) => [l.key, l])));
  const popular = $derived((app.config?.object_popular ?? []).map((k) => byKey.get(k)).filter((l) => l !== undefined));
  // Selected classes that are not popular stay visible while the full list is collapsed.
  const extra = $derived(selected.filter((k) => !popular.some((p) => p.key === k)).map((k) => byKey.get(k)).filter((l) => l !== undefined));
  const groups = $derived.by(() => {
    const q = query.trim().toLowerCase();
    const result = new Map<string, typeof labels>();
    for (const label of labels) {
      if (q && !label.name.toLowerCase().includes(q) && !label.group.toLowerCase().includes(q)) continue;
      result.set(label.group, [...(result.get(label.group) ?? []), label]);
    }
    return [...result.entries()];
  });

  const isOn = (key: string) => selected.includes(key);
  const full = $derived(selected.length >= max);

  function toggle(key: string) {
    selected = isOn(key) ? selected.filter((k) => k !== key) : [...selected, key];
  }
</script>

{#snippet chip(label: { key: string; name: string })}
  <button
    type="button"
    class="chip-btn"
    class:on={isOn(label.key)}
    aria-pressed={isOn(label.key)}
    disabled={!isOn(label.key) && full}
    onclick={() => toggle(label.key)}
  >
    {#if isOn(label.key)}<Icon name="check" size={13} strokeWidth={2.6} />{/if}
    {label.name}
  </button>
{/snippet}

<div class="col picker">
  {#if !showAll}
    <div class="row wrap chips" aria-label="Popular objects">
      {#each popular as label (label.key)}{@render chip(label)}{/each}
      {#each extra as label (label.key)}{@render chip(label)}{/each}
    </div>
  {:else}
    <input class="input" type="search" placeholder="Search {labels.length} objects, e.g. bicycle" bind:value={query} />
    {#each groups as [group, items] (group)}
      <div class="col" style="gap:6px">
        <span class="eyebrow">{group}</span>
        <div class="row wrap chips">{#each items as label (label.key)}{@render chip(label)}{/each}</div>
      </div>
    {:else}
      <p class="small muted">No object matches “{query}”.</p>
    {/each}
  {/if}

  <div class="row wrap" style="gap:var(--space-3)">
    <button type="button" class="btn sm ghost" onclick={() => ((showAll = !showAll), (query = ''))}>
      {showAll ? 'Show fewer' : `Show all ${labels.length} objects`}
    </button>
    <span class="xsmall" class:muted={!full} class:warn={full}>
      {selected.length} selected{full ? ` — the most one sensor can have (${max})` : ''}
    </span>
  </div>
</div>

<style>
  .picker {
    gap: var(--space-3);
  }
  .chips {
    gap: var(--space-2);
  }
  .chip-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    min-height: 36px;
    padding: 0 14px;
    border-radius: var(--radius-pill);
    border: 1px solid var(--c-border-strong);
    background: var(--c-surface);
    color: var(--c-text);
    font: inherit;
    font-size: var(--fs-md);
    cursor: pointer;
  }
  .chip-btn.on {
    background: var(--c-accent);
    border-color: var(--c-accent);
    color: var(--c-accent-ink);
    font-weight: 600;
  }
  .chip-btn:disabled {
    opacity: 0.45;
    cursor: default;
  }
  .eyebrow {
    font-size: var(--fs-xs);
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--c-faint);
  }
  .warn {
    color: var(--c-warn);
  }
</style>
