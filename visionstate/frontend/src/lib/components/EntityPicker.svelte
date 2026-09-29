<script lang="ts">
  // Search and pick Home Assistant entities (e.g. motion sensors) as chips.
  import { onMount } from 'svelte';
  import { api } from '../api';
  import type { HaEntity } from '../types';
  import { ENTITY_ID_PATTERN, ENTITY_SEARCH_LIMIT, TRIGGER_DOMAINS_FIRST } from '../ui';
  import Icon from './Icon.svelte';

  let { value = $bindable([]), max = 20 }: { value: string[]; max?: number } = $props();

  let all = $state<HaEntity[]>([]);
  let error = $state('');
  let query = $state('');
  let open = $state(false);

  onMount(async () => {
    try {
      all = await api.entities();
    } catch (err) {
      error = (err as Error).message;
    }
  });

  const byId = $derived(new Map(all.map((e) => [e.entity_id, e])));

  const rank = (domain: string) => {
    const i = TRIGGER_DOMAINS_FIRST.indexOf(domain);
    return i === -1 ? TRIGGER_DOMAINS_FIRST.length : i;
  };

  const matches = $derived.by(() => {
    const q = query.trim().toLowerCase();
    return all
      .filter((e) => !value.includes(e.entity_id) && e.domain !== 'camera')
      .filter((e) => !q || e.entity_id.includes(q) || e.name.toLowerCase().includes(q))
      .sort((a, b) => rank(a.domain) - rank(b.domain))
      .slice(0, ENTITY_SEARCH_LIMIT);
  });

  function add(id: string) {
    if (!value.includes(id) && value.length < max) value = [...value, id];
    query = '';
    open = false;
  }

  function remove(id: string) {
    value = value.filter((v) => v !== id);
  }

  function onkeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      e.preventDefault();
      const typed = query.trim();
      if (matches[0]) add(matches[0].entity_id);
      else if (ENTITY_ID_PATTERN.test(typed)) add(typed);
    } else if (e.key === 'Escape') open = false;
  }
</script>

<div class="col picker">
  {#if value.length}
    <div class="row wrap chips">
      {#each value as id (id)}
        {@const e = byId.get(id)}
        <span class="chip-entity">
          <span class="col" style="gap:0">
            <span class="small">{e?.name ?? id}</span>
            <span class="mono xsmall faint">{id}{e ? ` · ${e.state}` : ''}</span>
          </span>
          <button type="button" class="x" aria-label="Remove {id}" onclick={() => remove(id)}><Icon name="x" size={12} /></button>
        </span>
      {/each}
    </div>
  {/if}

  {#if value.length < max}
    <div class="search">
      <input
        class="input sm"
        placeholder={error ? 'Type an entity id, e.g. binary_sensor.garage_motion' : 'Search entities, e.g. motion'}
        bind:value={query}
        onfocus={() => (open = true)}
        onblur={() => setTimeout(() => (open = false), 150)}
        {onkeydown}
        aria-label="Add trigger entity"
      />
      {#if open && matches.length}
        <ul class="list" role="listbox">
          {#each matches as e (e.entity_id)}
            <li>
              <button type="button" onmousedown={(ev) => ev.preventDefault()} onclick={() => add(e.entity_id)}>
                <span class="small">{e.name}</span>
                <span class="mono xsmall faint">{e.entity_id} · {e.state}</span>
              </button>
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  {/if}
  {#if error}<p class="xsmall faint">Could not load entities from Home Assistant ({error}). You can still type an entity id and press Enter.</p>{/if}
</div>

<style>
  .picker {
    gap: var(--space-2);
  }
  .chips {
    gap: var(--space-2);
  }
  .chip-entity {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    padding: 6px 6px 6px 12px;
    border-radius: var(--radius-md);
    background: var(--c-surface-3);
    border: 1px solid var(--c-border-strong);
  }
  .x {
    background: none;
    border: none;
    color: var(--c-muted);
    cursor: pointer;
    padding: 4px;
    display: flex;
  }
  .search {
    position: relative;
  }
  .list {
    position: absolute;
    z-index: 20;
    left: 0;
    right: 0;
    top: calc(100% + 4px);
    max-height: 280px;
    overflow-y: auto;
    margin: 0;
    padding: 4px;
    list-style: none;
    background: var(--c-surface-2);
    border: 1px solid var(--c-border-strong);
    border-radius: var(--radius-md);
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
  }
  .list button {
    width: 100%;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 1px;
    padding: 6px 10px;
    border: none;
    border-radius: var(--radius-sm);
    background: none;
    color: var(--c-text);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .list button:hover {
    background: var(--c-surface-3);
  }
</style>
