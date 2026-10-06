<script lang="ts">
  // Wizard, "Detect" step of an object sensor: which objects, a test on a fresh frame with
  // everything the detector finds in the region (editable right there), and the entities.
  import { onMount, untrack } from 'svelte';
  import { api } from '../../lib/api';
  import DetectionBoxes from '../../lib/components/DetectionBoxes.svelte';
  import ObjectPicker from '../../lib/components/ObjectPicker.svelte';
  import RoiEditor from '../../lib/components/RoiEditor.svelte';
  import { haSlug } from '../../lib/format';
  import { objectCount } from '../../lib/objects';
  import type { Detection, Roi } from '../../lib/types';
  import HaPreview from './HaPreview.svelte';
  import TestCard from './TestCard.svelte';

  let {
    sourceType,
    source,
    name,
    roi = $bindable(),
    classes = $bindable(),
  }: { sourceType: string; source: string; name: string; roi: Roi | null; classes: string[] } = $props();

  let image = $state<string | null>(null);
  let detections = $state<Detection[]>([]);
  let busy = $state(false);
  let error = $state('');

  const found = $derived.by(() => {
    const counts = new Map<string, number>();
    for (const d of detections) counts.set(d.key, (counts.get(d.key) ?? 0) + 1);
    return [...counts].map(([key, n]) => ({ key, n, picked: classes.includes(key) }));
  });

  /** Detect on a fresh frame with the current region (also called when the light is on). */
  export async function test() {
    busy = true;
    error = '';
    try {
      const result = await api.previewDetect(sourceType, source, roi);
      image = result.image;
      detections = result.detections;
    } catch (err) {
      error = (err as Error).message;
    } finally {
      busy = false;
    }
  }

  onMount(() => {
    test();
  });

  // Detect again when the region changes (the frame is editable).
  let retestTimer: ReturnType<typeof setTimeout> | undefined;
  $effect(() => {
    const snapshot = JSON.stringify(roi);
    if (!untrack(() => image)) return;
    clearTimeout(retestTimer);
    retestTimer = setTimeout(() => snapshot && test(), 500);
    return () => clearTimeout(retestTimer);
  });
</script>

<div class="col" style="gap:var(--space-3)">
  <h3>Which objects?</h3>
  <ObjectPicker bind:selected={classes} />
</div>
<TestCard {busy} onretest={test}>
  {#snippet status()}
    {#if busy && !image}
      <span class="muted">Looking… the first check loads the detector, which takes a few seconds.</span>
    {:else if error}
      <span class="danger-text">{error}</span>
    {:else if found.length}
      Found {found.map((f) => objectCount(f.n, f.key) + (f.picked ? '' : ' (not selected)')).join(', ')}
    {:else if image}
      <span class="muted">Nothing found in the region right now — that is fine if it is empty.</span>
    {/if}
    {#if busy && image}<span class="faint"> (looking…)</span>{/if}
  {/snippet}
  {#if image}
    <RoiEditor src={image} bind:roi editable>
      <DetectionBoxes {detections} {classes} />
    </RoiEditor>
  {/if}
</TestCard>
<HaPreview>
  {#each classes as key (key)}
    <span class="mono small">binary_sensor.{haSlug(`${name} ${key}`)}</span>
    <span class="mono xsmall muted">sensor.{haSlug(`${name} ${key} count`)}</span>
  {:else}
    <span class="xsmall muted">Pick at least one object.</span>
  {/each}
</HaPreview>

<style>
  .danger-text {
    color: var(--c-danger);
  }
</style>
