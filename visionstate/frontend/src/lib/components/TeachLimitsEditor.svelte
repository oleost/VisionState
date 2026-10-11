<script lang="ts">
  // Edits the teaching limits of object sensors. An empty field inherits (shown in grey): on the
  // Settings page the state model's default, on a sensor the global value.
  import { app } from '../app.svelte';
  import type { TeachKey, TeachLimits, TeachOverrides } from '../types';
  import { TEACH_FIELDS } from '../ui';
  import Icon from './Icon.svelte';

  let {
    value = $bindable(),
    inherited,
    inheritedFrom,
  }: {
    value: TeachOverrides;
    /** What an empty field uses. */
    inherited: TeachLimits;
    /** Where that comes from, e.g. "the global value". */
    inheritedFrom: string;
  } = $props();

  const limits = $derived(app.config?.teach.limits);
  const pctOf = (n: number) => Math.round(n * 100);
  const effective = (key: TeachKey) => value[key] ?? inherited[key];
  const conflict = $derived(
    effective('ask_similarity') > effective('match_similarity') || effective('keep_similarity') > effective('match_similarity'),
  );

  function set(key: TeachKey, raw: string) {
    value[key] = raw.trim() === '' ? null : Number(raw) / 100;
  }
</script>

<div class="col limits">
  {#if limits}
    {#each TEACH_FIELDS as f (f.key)}
      {@const own = value[f.key] !== null && value[f.key] !== undefined}
      <div class="line" data-testid={`teach-${f.key}`}>
        <label class="col name" for={`teach-${f.key}`}>
          <span>{f.label}</span>
          <span class="xsmall faint">{f.help}</span>
        </label>
        <span class="row input-row">
          <input
            id={`teach-${f.key}`}
            class="input sm num"
            class:own
            type="number"
            min={pctOf(limits[f.key][0])}
            max={pctOf(limits[f.key][1])}
            step="1"
            value={own ? pctOf(value[f.key] as number) : ''}
            placeholder={String(pctOf(inherited[f.key]))}
            oninput={(e) => set(f.key, e.currentTarget.value)}
            aria-label={f.label}
          />
          <span class="unit small muted">%</span>
          <button
            class="btn sm ghost reset"
            class:hidden={!own}
            disabled={!own}
            onclick={() => (value[f.key] = null)}
            aria-label={`Use ${inheritedFrom} (${pctOf(inherited[f.key])}%)`}
            title={`Use ${inheritedFrom}`}
          >
            <Icon name="x" size={14} />
          </button>
        </span>
      </div>
    {/each}
  {/if}
  {#if conflict}
    <div class="notice warn small">The second and third values must not be above the first.</div>
  {/if}
  <p class="xsmall faint">Empty fields (in grey) use {inheritedFrom}. 100% is the very same picture.</p>
</div>

<style>
  .limits {
    gap: var(--space-3);
  }
  .line {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-4);
    color: var(--c-text-2);
  }
  .name {
    gap: 2px;
    min-width: 0;
  }
  .input-row {
    gap: var(--space-2);
    flex-shrink: 0;
  }
  .num {
    width: 72px;
  }
  .num::placeholder {
    color: var(--c-faint);
  }
  .num:not(.own) {
    color: var(--c-faint);
  }
  .unit {
    width: 14px;
  }
  .reset.hidden {
    visibility: hidden;
  }
</style>
