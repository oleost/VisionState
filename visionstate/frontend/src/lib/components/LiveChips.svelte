<script lang="ts">
  // What a sensor finds right now, one chip each: the objects an object sensor sees (with their
  // count), the sounds a sound sensor hears. `overlay` = one compact pill with `summary` (dashboard).
  // Their chips and summary come from objectChips (objects.ts) and soundChips (sound.ts).
  type Chip = { key: string; name: string; on: boolean; color: string; count?: number };
  let { chips, summary, overlay = false }: { chips: Chip[]; summary: string; overlay?: boolean } = $props();

  const anyOn = $derived(chips.some((c) => c.on));
</script>

{#if overlay}
  <span class="pill overlay">
    <span class="dot" style:background={anyOn ? 'var(--c-accent)' : 'var(--c-unknown)'}></span>
    {summary}
  </span>
{:else}
  <span class="row wrap chips">
    {#each chips as c (c.key)}
      <span class="pill" class:off={!c.on}>
        <span class="dot" style:background={c.on ? c.color : 'var(--c-unknown)'}></span>
        {c.name}
        {#if c.count !== undefined}<span class="count mono">{c.on ? c.count : 0}</span>{/if}
      </span>
    {/each}
  </span>
{/if}

<style>
  .chips {
    gap: var(--space-2);
  }
  .pill {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    height: 28px;
    padding: 0 12px;
    border-radius: var(--radius-pill);
    background: var(--c-surface-3);
    font-size: var(--fs-md);
    font-weight: 600;
    white-space: nowrap;
  }
  .pill.off {
    color: var(--c-muted);
    font-weight: 500;
  }
  .pill.overlay {
    background: var(--c-overlay);
    height: 30px;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .count {
    font-weight: 400;
    font-size: var(--fs-sm);
    color: var(--c-text-2);
  }
</style>
