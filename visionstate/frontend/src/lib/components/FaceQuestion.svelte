<script lang="ts">
  // A review question about a face: "Is this Kari?" (not sure enough to say so by itself) or "Who
  // is this?" (a face nobody was taught for, when the sensor asks about those). The face close-up
  // sits beside the frame (stacked on phones), with one of the person's taught faces to compare.
  // A face model's similarity means nothing to people, so no percentage is shown.
  import { api } from '../api';
  import { app, toastError } from '../app.svelte';
  import type { ObjectQuestion, ReviewItem } from '../types';
  import DetectionBoxes from './DetectionBoxes.svelte';
  import ReviewAnswers, { type Answer } from './ReviewAnswers.svelte';
  import RoiEditor from './RoiEditor.svelte';

  let {
    item,
    question,
    given,
    onanswer,
  }: {
    item: ReviewItem;
    question: ObjectQuestion;
    /** The answer given before, when the item is opened again. */
    given: string | null | undefined;
    onanswer: (text: string, answer: string | null) => void;
  } = $props();

  const who = $derived(question.label === app.config?.face_unknown_key);
  const people = $derived(item.sensor.labels.filter((l) => l.face));
  const name = $derived(people.find((p) => p.key === question.label)?.name ?? question.label);
  const others = $derived(people.filter((p) => p.key !== question.label));
  let busy = $state(false);

  // The close-up: the face with some room around it, as a background crop of the frame.
  const src = $derived(api.historyImageUrl(item.id));
  let natural = $state<{ w: number; h: number } | null>(null);
  $effect(() => {
    const img = new Image();
    img.onload = () => (natural = { w: img.naturalWidth, h: img.naturalHeight });
    img.src = src;
  });
  const crop = $derived.by(() => {
    if (!natural || !question.face) return null;
    const [fx1, fy1, fx2, fy2] = question.face;
    const mx = (fx2 - fx1) * 0.3;
    const my = (fy2 - fy1) * 0.3;
    const [x1, y1, x2, y2] = [Math.max(0, fx1 - mx), Math.max(0, fy1 - my), Math.min(1, fx2 + mx), Math.min(1, fy2 + my)];
    const w = x2 - x1;
    const h = y2 - y1;
    return {
      aspect: (w * natural.w) / (h * natural.h),
      size: `${100 / w}% ${100 / h}%`,
      position: `${w < 1 ? (x1 / (1 - w)) * 100 : 0}% ${h < 1 ? (y1 / (1 - h)) * 100 : 0}%`,
    };
  });

  async function send(action: Answer, person?: string, newName?: string) {
    if (busy) return;
    busy = true;
    try {
      await api.answerFace(item.id, action === 'pick' ? 'person' : action, person, newName);
      const text =
        action === 'yes'
          ? `✓ ${name}`
          : action === 'pick'
            ? `✓ ${people.find((p) => p.key === person)?.name ?? person}`
            : action === 'new'
              ? `✓ ${newName} (new)`
              : action === 'no'
                ? `✓ Not ${name}`
                : 'Skipped';
      onanswer(text, person ?? (action === 'new' ? 'new' : action === 'skip' ? null : action));
    } catch (err) {
      toastError(err);
    } finally {
      busy = false;
    }
  }
</script>

<div class="pair">
  <div class="frame">
    <RoiEditor {src} roi={item.sensor.roi}>
      <DetectionBoxes
        detections={[{ key: 'person', score: item.confidence, box: question.box, face: { box: question.face, problem: null } }]}
        classes={['person']}
        labels={false}
      />
    </RoiEditor>
  </div>
  <div class="faces">
    {#if crop}
      <figure>
        <div
          class="closeup"
          style:aspect-ratio={crop.aspect}
          style:background-image="url({src})"
          style:background-size={crop.size}
          style:background-position={crop.position}
        ></div>
        <figcaption class="xsmall faint">This face</figcaption>
      </figure>
    {/if}
    {#if !who && question.example}
      <figure>
        <img src={api.faceImageUrl(question.example)} alt="A face taught as {name}" />
        <figcaption class="xsmall faint">Taught as {name}</figcaption>
      </figure>
    {/if}
  </div>
</div>

<ReviewAnswers
  asked={who ? null : name}
  choices={who ? people : others}
  noun="person"
  noLabel={who ? null : 'No, someone I do not know'}
  skipLabel={who ? 'Don’t know' : 'Skip'}
  footnote="A name you give is taught: this face is added to that person."
  {given}
  {busy}
  onanswer={send}
>
  {#snippet question()}
    {#if who}
      Who is this?
      <span class="small muted">A face this sensor does not know.</span>
    {:else}
      <span>Is this <strong>{name}</strong>?</span>
      <span class="small muted">Probably, but not sure enough to say so by itself.</span>
    {/if}
  {/snippet}
</ReviewAnswers>

<style>
  .pair {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 140px;
    gap: var(--space-4);
    align-items: start;
  }
  .faces {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  figure {
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .closeup,
  figure img {
    width: 100%;
    border-radius: var(--radius-sm);
    background-repeat: no-repeat;
    background-color: var(--c-sunken);
  }
  figure img {
    aspect-ratio: 1;
    object-fit: cover;
  }
  .frame {
    border-radius: var(--radius-md);
    overflow: hidden;
  }
  @media (max-width: 600px) {
    .pair {
      grid-template-columns: minmax(0, 1fr);
    }
    .faces {
      flex-direction: row;
    }
    .faces figure {
      width: 120px;
    }
  }
</style>
