<script lang="ts">
  // "From your answers": every threshold the sensor decides by — any kind — with how the answers in
  // Review fare at it and a suggested one (ThresholdAdvice, backend api/advice.py). Only thresholds
  // that have answers are listed. `onapply` tells the page, so a form it keeps shows the new value.
  import { untrack } from 'svelte';
  import { api } from '../api';
  import { toast, toastError } from '../app.svelte';
  import { pct } from '../format';
  import { href, paths } from '../router.svelte';
  import type { AdviceItem, Sensor } from '../types';
  import ThresholdAdvice from './ThresholdAdvice.svelte';

  let {
    sensor,
    onapply = () => {},
  }: { sensor: Sensor; onapply?: (key: string, threshold: number) => void } = $props();

  let items = $state<AdviceItem[] | null>(null);
  const answered = $derived((items ?? []).filter((i) => i.advice.yes + i.advice.no > 0));
  // What tells a sound, an own label or a face apart when no threshold does.
  const hint = $derived(
    sensor.kind === 'sound'
      ? 'Teach it with clips on Own sounds.'
      : sensor.kind === 'objects'
        ? 'Teach more boxes or faces.'
        : sensor.kind === 'single_state'
          ? 'Label more frames.'
          : '',
  );

  async function load() {
    try {
      items = (await api.sensorAdvice(sensor.id)).items;
    } catch (err) {
      toastError(err);
    }
  }
  untrack(() => load());

  async function apply(item: AdviceItem, threshold: number) {
    try {
      await api.applyAdvice(sensor.id, item.key, threshold);
      toast(`${item.name}: ${pct(threshold)}`);
      onapply(item.key, threshold);
      await load();
    } catch (err) {
      toastError(err);
    }
  }
</script>

{#if items?.length}
  <section class="card pad col advice-card" data-testid="advice-card">
    <h3>From your answers</h3>
    {#if answered.length}
      {#each answered as item (item.key)}
        <div class="col item" data-testid="advice-{item.key}">
          <strong class="small">{item.name} <span class="faint">· {pct(item.threshold)}</span></strong>
          <ThresholdAdvice advice={item.advice} verb={item.verb} {hint} onapply={(t) => apply(item, t)} />
        </div>
      {/each}
    {:else}
      <p class="small muted">
        Your answers in <a href={href(paths.review(sensor.id))}>Review</a> are counted here: once there are a few, it suggests a
        threshold that keeps more of the right ones and fewer of the wrong ones.
      </p>
    {/if}
  </section>
{/if}

<style>
  .advice-card {
    gap: var(--space-3);
  }
  .item {
    gap: var(--space-2);
  }
</style>
