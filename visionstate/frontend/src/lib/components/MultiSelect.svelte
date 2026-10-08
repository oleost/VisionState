<script lang="ts" module>
  export interface Option {
    value: string;
    label: string;
    count?: number;
    color?: string;
  }
</script>

<script lang="ts">
  // Pick any number of values from a list: a button showing what is chosen, opening a list below it
  // (a bottom sheet on phones). Nothing chosen = no limit ("All"). Taps only, no hover.
  import Icon from './Icon.svelte';

  let {
    label,
    options,
    selected = $bindable([]),
    all = 'All',
    onchange = () => {},
  }: { label: string; options: Option[]; selected: string[]; all?: string; onchange?: (selected: string[]) => void } = $props();

  let open = $state(false);
  let root: HTMLDivElement;

  const summary = $derived(
    selected.length === 0
      ? all
      : selected.length === 1
        ? (options.find((o) => o.value === selected[0])?.label ?? selected[0])
        : `${selected.length} selected`,
  );

  function set(values: string[]) {
    selected = values;
    onchange(values);
  }
  const toggle = (value: string) => set(selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value]);

  function outside(e: PointerEvent) {
    if (open && !root.contains(e.target as Node)) open = false;
  }
  function onkey(e: KeyboardEvent) {
    if (open && e.key === 'Escape') open = false;
  }
</script>

<svelte:window onpointerdown={outside} onkeydown={onkey} />

<div class="ms" bind:this={root}>
  <button type="button" class="trigger" class:set={selected.length > 0} aria-haspopup="listbox" aria-expanded={open} onclick={() => (open = !open)}>
    <span class="lbl">{label}</span>
    <span class="val">{summary}</span>
    <Icon name={open ? 'chevron-up' : 'chevron-down'} size={14} />
  </button>
  {#if open}
    <div class="backdrop" role="presentation" onclick={() => (open = false)}></div>
    <div class="panel">
      <div class="row panel-head">
        <strong class="small">{label}</strong>
        <span class="spacer"></span>
        {#if selected.length}<button type="button" class="btn sm ghost" onclick={() => set([])}>Clear</button>{/if}
        <button type="button" class="btn sm" onclick={() => (open = false)}>Done</button>
      </div>
      <div class="options" role="listbox" aria-multiselectable="true" aria-label={label}>
        {#each options as o (o.value)}
          {@const on = selected.includes(o.value)}
          <button type="button" role="option" aria-selected={on} class="opt" class:on onclick={() => toggle(o.value)}>
            <span class="box">{#if on}<Icon name="check" size={12} strokeWidth={2.6} />{/if}</span>
            {#if o.color}<span class="dot" style:background={o.color}></span>{/if}
            <span class="name">{o.label}</span>
            {#if o.count !== undefined}<span class="mono xsmall faint">{o.count}</span>{/if}
          </button>
        {:else}
          <p class="small muted empty">Nothing to choose here yet.</p>
        {/each}
      </div>
    </div>
  {/if}
</div>

<style>
  .ms {
    position: relative;
    min-width: 0;
  }
  .trigger {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    max-width: 100%;
    height: 36px;
    padding: 0 var(--space-3);
    border-radius: var(--radius-md);
    border: 1px solid var(--c-border-strong);
    background: var(--c-surface-2);
    color: var(--c-text);
    font: inherit;
    font-size: var(--fs-md);
    cursor: pointer;
  }
  .trigger.set {
    border-color: var(--c-accent-line);
    background: var(--c-accent-soft);
  }
  .lbl {
    color: var(--c-muted);
  }
  .val {
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }
  .backdrop {
    display: none;
  }
  .panel {
    position: absolute;
    z-index: 30;
    top: calc(100% + 6px);
    left: 0;
    width: max(100%, 280px);
    max-width: calc(100vw - 32px);
    background: var(--c-surface);
    border: 1px solid var(--c-border-strong);
    border-radius: var(--radius-lg);
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
    display: flex;
    flex-direction: column;
  }
  .panel-head {
    gap: var(--space-2);
    padding: var(--space-2) var(--space-2) var(--space-2) var(--space-4);
    border-bottom: 1px solid var(--c-border);
  }
  .options {
    max-height: 320px;
    overflow-y: auto;
    padding: var(--space-1);
    display: flex;
    flex-direction: column;
  }
  .opt {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    min-height: var(--touch);
    padding: 0 var(--space-3);
    border: none;
    border-radius: var(--radius-md);
    background: none;
    color: var(--c-text);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .opt.on {
    background: var(--c-surface-3);
  }
  .box {
    width: 18px;
    height: 18px;
    flex-shrink: 0;
    border-radius: 5px;
    border: 1.5px solid var(--c-border-dashed);
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }
  .opt.on .box {
    background: var(--c-accent);
    border-color: var(--c-accent);
    color: var(--c-accent-ink);
  }
  .name {
    flex: 1;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .empty {
    padding: var(--space-3);
  }
  @media (max-width: 600px) {
    .backdrop {
      display: block;
      position: fixed;
      inset: 0;
      z-index: 40;
      background: rgba(5, 7, 9, 0.6);
    }
    .panel {
      position: fixed;
      z-index: 41;
      top: auto;
      left: 0;
      right: 0;
      bottom: 0;
      width: auto;
      max-width: none;
      max-height: 80vh;
      border-radius: var(--radius-lg) var(--radius-lg) 0 0;
      padding-bottom: env(safe-area-inset-bottom);
    }
    .options {
      max-height: none;
      flex: 1;
    }
  }
</style>
