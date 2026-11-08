<script lang="ts">
  // A row of tabs (links) that never scrolls sideways: the tabs that do not fit go under "More", a
  // menu at the end of the row. The active tab always stays in the row, so the page says where you
  // are. Every tab is measured in a hidden copy of the row; measuring happens in a timeout, not in
  // the ResizeObserver's callback (see `measure` in DetectionBoxes.svelte for why).
  import Icon from './Icon.svelte';

  export type Tab = { id: string; label: string; href: string; count?: number };

  let { tabs, active, label }: { tabs: Tab[]; active: string | undefined; label: string } = $props();

  let rowWidth = $state(0);
  let widths = $state<number[]>([]);
  let moreWidth = $state(0);
  let open = $state(false);
  let wrap = $state<HTMLElement>();

  function measure(node: HTMLElement) {
    // Read here so the attachment runs again (measures again) when a tab comes, goes or changes
    // its text: the hidden row itself has no size to observe.
    void tabs.map((t) => `${t.label}${t.count ?? ''}`).join('|');
    let timer: ReturnType<typeof setTimeout> | undefined;
    const take = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const items = [...node.querySelectorAll<HTMLElement>('[data-tab]')];
        widths = items.map((el) => el.offsetWidth);
        moreWidth = node.querySelector<HTMLElement>('[data-more]')?.offsetWidth ?? 0;
        rowWidth = node.parentElement?.clientWidth ?? 0;
      });
    };
    const observer = new ResizeObserver(take);
    observer.observe(node);
    if (node.parentElement) observer.observe(node.parentElement);
    take();
    return () => {
      clearTimeout(timer);
      observer.disconnect();
    };
  }

  // Which tabs stay in the row: as many as fit in order, with room left for "More"; the active
  // one replaces the last that fit when it would otherwise be in the menu.
  const shown = $derived.by(() => {
    const all = tabs.map((t) => t.id);
    if (!rowWidth || widths.length !== tabs.length) return all;
    const gap = 4; // var(--space-1) between tabs
    const total = widths.reduce((a, w) => a + w + gap, 0) - gap;
    if (total <= rowWidth) return all;
    const budget = rowWidth - moreWidth - gap;
    const fits = (ids: string[]) => ids.reduce((a, id) => a + widths[all.indexOf(id)] + gap, 0) <= budget;
    const keep: string[] = [];
    for (const id of all) {
      if (fits([...keep, id])) keep.push(id);
      else break;
    }
    if (active && !keep.includes(active)) {
      while (keep.length && !fits([...keep, active])) keep.pop();
      keep.push(active);
      keep.sort((a, b) => all.indexOf(a) - all.indexOf(b));
    }
    return keep;
  });
  const inRow = $derived(tabs.filter((t) => shown.includes(t.id)));
  const inMenu = $derived(tabs.filter((t) => !shown.includes(t.id)));
  const menuCount = $derived(inMenu.reduce((a, t) => a + (t.count ?? 0), 0));

  // The menu closes on a tap outside it, on Escape and when a tab is chosen.
  $effect(() => {
    if (!open) return;
    const outside = (e: PointerEvent) => {
      if (wrap && !wrap.contains(e.target as Node)) open = false;
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') open = false;
    };
    window.addEventListener('pointerdown', outside);
    window.addEventListener('keydown', key);
    return () => {
      window.removeEventListener('pointerdown', outside);
      window.removeEventListener('keydown', key);
    };
  });
  $effect(() => {
    void active;
    open = false;
  });
</script>

{#snippet count(n: number | undefined)}
  {#if n}<span class="count">{n}</span>{/if}
{/snippet}

<div class="bar">
  <nav aria-label={label}>
    {#each inRow as t (t.id)}
      <a href={t.href} class="tab" class:active={t.id === active} aria-current={t.id === active ? 'page' : undefined}>
        {t.label}{@render count(t.count)}
      </a>
    {/each}
    {#if inMenu.length}
      <div class="more" bind:this={wrap}>
        <button type="button" class="tab" aria-haspopup="true" aria-expanded={open} onclick={() => (open = !open)}>
          More{@render count(menuCount)}<Icon name="chevron-down" size={14} />
        </button>
        {#if open}
          <div class="menu" role="menu">
            {#each inMenu as t (t.id)}
              <a href={t.href} role="menuitem" onclick={() => (open = false)}>{t.label}{@render count(t.count)}</a>
            {/each}
          </div>
        {/if}
      </div>
    {/if}
  </nav>
  <div class="measure" aria-hidden="true" {@attach measure}>
    {#each tabs as t (t.id)}
      <span class="tab" data-tab>{t.label}{@render count(t.count)}</span>
    {/each}
    <span class="tab" data-more>More{@render count(1)}<Icon name="chevron-down" size={14} /></span>
  </div>
</div>

<style>
  .bar {
    position: relative;
    min-width: 0;
  }
  nav {
    display: flex;
    gap: var(--space-1);
    /* Until the tabs are measured they may not fit: never wider than the page (clip, unlike
       hidden, leaves the menu below the row visible). */
    overflow-x: clip;
  }
  .tab {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 10px 14px;
    font: inherit;
    font-weight: 500;
    color: var(--c-muted);
    background: none;
    border: 0;
    border-bottom: 2px solid transparent;
    white-space: nowrap;
    flex-shrink: 0;
    cursor: pointer;
  }
  .tab.active {
    color: var(--c-text);
    border-bottom-color: var(--c-accent);
  }
  .tab:hover,
  .more button[aria-expanded='true'] {
    color: var(--c-text);
  }
  .measure {
    position: absolute;
    left: 0;
    top: 0;
    display: flex;
    gap: var(--space-1);
    width: 0; /* adds nothing to the page's width */
    overflow: hidden;
    visibility: hidden;
    pointer-events: none;
  }
  .more {
    position: relative;
  }
  .menu {
    position: absolute;
    right: 0;
    top: calc(100% + 4px);
    z-index: 30;
    min-width: 200px;
    max-width: calc(100vw - 32px);
    padding: var(--space-1);
    display: flex;
    flex-direction: column;
    background: var(--c-surface-2);
    border: 1px solid var(--c-border);
    border-radius: var(--radius-md);
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
  }
  .menu a {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    padding: 12px 14px;
    border-radius: var(--radius-sm);
    color: var(--c-text);
    font-weight: 500;
    white-space: nowrap;
  }
  .menu a:hover {
    background: var(--c-surface-3);
  }
  .count {
    min-width: 18px;
    height: 18px;
    padding: 0 5px;
    border-radius: var(--radius-pill);
    background: var(--c-surface-3);
    font-size: var(--fs-xs);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: var(--c-text-2);
  }
</style>
