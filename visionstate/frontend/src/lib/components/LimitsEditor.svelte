<script lang="ts">
  // Edits tunable limits that inherit (teaching of object sensors, faces). An empty field inherits
  // (shown in grey): on the Settings page the default, on a sensor the global value. Similarities
  // are shown in percent, pixel values as they are.
  import Icon from './Icon.svelte';

  type Field = { key: string; label: string; help: string; unit?: 'px' };

  let {
    value = $bindable(),
    inherited,
    inheritedFrom,
    fields,
    limits,
    below = [],
    id,
    note = '',
  }: {
    value: Record<string, number | null | undefined>;
    /** What an empty field uses. */
    inherited: Record<string, number>;
    /** Where that comes from, e.g. "the global value". */
    inheritedFrom: string;
    fields: readonly Field[];
    /** Lowest and highest value per key. */
    limits: Record<string, readonly [number, number]>;
    /** Pairs [a, b]: a must not be above b; with the message shown when it is. */
    below?: readonly { pairs: readonly (readonly [string, string])[]; message: string }[];
    /** Prefix of the inputs' ids and test ids. */
    id: string;
    note?: string;
  } = $props();

  const pct = (f: Field) => f.unit !== 'px';
  const shown = (f: Field, n: number) => (pct(f) ? Math.round(n * 100) : Math.round(n));
  const effective = (key: string) => value[key] ?? inherited[key];
  const conflicts = $derived(below.filter((b) => b.pairs.some(([a, c]) => effective(a) > effective(c))));

  function set(f: Field, raw: string) {
    value[f.key] = raw.trim() === '' ? null : pct(f) ? Number(raw) / 100 : Number(raw);
  }
</script>

<div class="col limits">
  {#each fields as f (f.key)}
    {@const own = value[f.key] !== null && value[f.key] !== undefined}
    <div class="line" data-testid={`${id}-${f.key}`}>
      <label class="col name" for={`${id}-${f.key}`}>
        <span>{f.label}</span>
        <span class="xsmall faint">{f.help}</span>
      </label>
      <span class="row input-row">
        <input
          id={`${id}-${f.key}`}
          class="input sm num"
          class:own
          type="number"
          min={shown(f, limits[f.key][0])}
          max={shown(f, limits[f.key][1])}
          step="1"
          value={own ? shown(f, value[f.key] as number) : ''}
          placeholder={String(shown(f, inherited[f.key]))}
          oninput={(e) => set(f, e.currentTarget.value)}
          aria-label={f.label}
        />
        <span class="unit small muted">{pct(f) ? '%' : 'px'}</span>
        <button
          class="btn sm ghost reset"
          class:hidden={!own}
          disabled={!own}
          onclick={() => (value[f.key] = null)}
          aria-label={`Use ${inheritedFrom} (${shown(f, inherited[f.key])}${pct(f) ? '%' : ' px'})`}
          title={`Use ${inheritedFrom}`}
        >
          <Icon name="x" size={14} />
        </button>
      </span>
    </div>
  {/each}
  {#each conflicts as c (c.message)}
    <div class="notice warn small">{c.message}</div>
  {/each}
  <p class="xsmall faint">Empty fields (in grey) use {inheritedFrom}.{note ? ` ${note}` : ''}</p>
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
    width: 18px;
  }
  .reset.hidden {
    visibility: hidden;
  }
</style>
