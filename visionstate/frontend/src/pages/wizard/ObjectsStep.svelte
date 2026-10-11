<script lang="ts">
  // Wizard, "Detect" step of an object sensor: which objects, a test on a fresh frame with
  // everything the detector finds in the region (editable right there), and the entities.
  import { onMount, untrack } from 'svelte';
  import { api } from '../../lib/api';
  import DetectionBoxes from '../../lib/components/DetectionBoxes.svelte';
  import FaceConsent from '../../lib/components/FaceConsent.svelte';
  import ObjectPicker from '../../lib/components/ObjectPicker.svelte';
  import RoiEditor from '../../lib/components/RoiEditor.svelte';
  import { haSlug } from '../../lib/format';
  import { objectCount } from '../../lib/objects';
  import type { Detection, FaceModelInfo, Roi } from '../../lib/types';
  import HaPreview from './HaPreview.svelte';
  import TestCard from './TestCard.svelte';

  let {
    sourceType,
    source,
    name,
    roi = $bindable(),
    classes = $bindable(),
    faces = $bindable(),
  }: {
    sourceType: string;
    source: string;
    name: string;
    roi: Roi | null;
    classes: string[];
    /** Recognise people by their face (asked first, with what is stored and downloaded). */
    faces: boolean;
  } = $props();

  let faceModel = $state<FaceModelInfo | null>(null);
  let askingFaces = $state(false);
  api
    .settings()
    .then((s) => (faceModel = s.face_models.find((m) => m.id === s.face_model) ?? null))
    .catch(() => {});

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
  {#if classes.includes('person')}
    <label class="check" data-testid="wizard-faces">
      <input
        type="checkbox"
        checked={faces}
        onclick={(e) => {
          if (faces) return (faces = false);
          e.preventDefault(); // on only after the confirmation
          askingFaces = true;
        }}
      />
      <span class="col" style="gap:2px">
        <strong class="small">Recognise people by their face</strong>
        <span class="xsmall faint">
          You name the people after it is made (tap a person on its Live tab); each gets an on/off sensor.
        </span>
      </span>
    </label>
  {/if}
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

{#if askingFaces}
  <FaceConsent
    name={name.trim()}
    model={faceModel}
    onclose={() => (askingFaces = false)}
    onconfirm={() => {
      askingFaces = false;
      faces = true;
    }}
  />
{/if}

<style>
  .check {
    align-items: flex-start;
  }
  .check input {
    margin-top: 3px;
  }
  .danger-text {
    color: var(--c-danger);
  }
</style>
