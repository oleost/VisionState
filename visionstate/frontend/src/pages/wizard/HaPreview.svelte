<script lang="ts">
  // The wizard's "In Home Assistant" card: the entities the new sensor will get.
  import type { Snippet } from 'svelte';

  let { children }: { children: Snippet } = $props();
</script>

<div class="card pad col preview">
  <span class="eyebrow">In Home Assistant</span>
  {@render children()}
</div>

<style>
  .preview {
    max-width: 520px;
    background: var(--c-bg);
    gap: var(--space-2);
    overflow-wrap: anywhere; /* long entity ids on phones */
  }
  .eyebrow {
    font-size: var(--fs-sm);
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--c-faint);
  }
</style>
