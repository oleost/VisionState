<script lang="ts">
  // A threshold suggested from review answers — the same for every sensor kind (backend advice.py):
  // how the "yes" and "no" answers fare at the threshold now, and at the suggested one, which is
  // used with one tap. When no threshold separates them, it says so: then taught examples have to
  // tell them apart. `verb` is what passing the threshold means for the kind ("heard", "published").
  import { app } from '../app.svelte';
  import { pct } from '../format';
  import type { ThresholdAdvice } from '../types';

  let {
    advice,
    verb,
    hint = '',
    onapply,
  }: { advice: ThresholdAdvice; verb: string; hint?: string; onapply: (threshold: number) => Promise<void> } = $props();

  let busy = $state(false);
  const total = $derived(advice.yes + advice.no);
  const needed = $derived(app.config?.advice.min_answers ?? 3);

  const fare = (c: { yes_passed: number; no_passed: number }) =>
    `${c.yes_passed} of ${advice.yes} “yes” ${verb}, ${c.no_passed} of ${advice.no} “no”`;

  async function apply(threshold: number) {
    busy = true;
    try {
      await onapply(threshold);
    } finally {
      busy = false;
    }
  }
</script>

{#if total}
  <div class="col advice" data-testid="threshold-advice">
    <span class="small">
      From your answers ({advice.yes} yes, {advice.no} no) — at {pct(advice.current.threshold)}: {fare(advice.current)}.
    </span>
    {#if total < needed}
      <span class="xsmall muted">A threshold is suggested from {needed} answers.</span>
    {:else if advice.suggested}
      <div class="row wrap suggest">
        <span class="small">At <strong>{pct(advice.suggested.threshold)}</strong>: {fare(advice.suggested)}.</span>
        <button type="button" class="btn sm primary" disabled={busy} onclick={() => apply(advice.suggested!.threshold)}>
          Use {pct(advice.suggested.threshold)}
        </button>
      </div>
    {:else if !advice.separable && total >= 2 && advice.yes && advice.no}
      <span class="xsmall muted">
        {pct(advice.current.threshold)} is as good as a threshold gets for these answers, but none keeps every “yes” and no
        “no”.{hint ? ` ${hint}` : ''}
      </span>
    {:else}
      <span class="xsmall muted">The threshold suits your answers.</span>
    {/if}
  </div>
{/if}

<style>
  .advice {
    gap: var(--space-2);
    padding: var(--space-3);
    border-radius: var(--radius-md);
    background: var(--c-sunken);
  }
  .suggest {
    gap: var(--space-3);
    align-items: center;
  }
</style>
