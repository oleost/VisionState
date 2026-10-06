<script lang="ts">
  // The wizard's test of an object or reading sensor on a fresh frame: what was found or read,
  // "Test again", and the frame below it (``children``).
  import type { Snippet } from 'svelte';
  import Icon from '../../lib/components/Icon.svelte';

  let {
    busy,
    onretest,
    status,
    children,
  }: { busy: boolean; onretest: () => void; status: Snippet; children?: Snippet } = $props();
</script>

<div class="card col test">
  <div class="row wrap bar">
    <span class="small">{@render status()}</span>
    <span class="spacer"></span>
    <button class="btn sm" disabled={busy} onclick={onretest}><Icon name="refresh" size={14} /> Test again</button>
  </div>
  {@render children?.()}
</div>

<style>
  .test {
    max-width: 900px;
    overflow: hidden;
  }
  .bar {
    padding: 12px 16px;
    gap: var(--space-3);
  }
</style>
