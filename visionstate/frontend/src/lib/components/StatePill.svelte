<script lang="ts">
  import { pct } from '../format';

  let {
    name,
    color,
    confidence = null,
    overlay = false,
  }: { name: string; color: string; confidence?: number | null; overlay?: boolean } = $props();
</script>

<span class="pill" class:overlay>
  <span class="dot" style:background={color}></span>
  {name}
  {#if confidence !== null}<span class="conf mono">{pct(confidence)}</span>{/if}
</span>

<style>
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
  .pill.overlay {
    background: var(--c-overlay);
    height: 30px;
  }
  .conf {
    font-weight: 400;
    font-size: var(--fs-sm);
    color: var(--c-text-2);
  }
</style>
