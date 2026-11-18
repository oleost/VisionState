<script lang="ts">
  // "Was this a siren?" — a sound that was only just heard. Listen to its clip (watch it, on a
  // camera) and answer: yes, no, it was one of the sensor's own sounds, or a new one. The answer is
  // kept, and teaches the clip: as the sound (an own or a built-in one), or as not it.
  import { api } from '../../api';
  import { toastError } from '../../app.svelte';
  import { pct } from '../../format';
  import type { ReviewItem } from '../../types';
  import ReviewAnswers, { type Answer } from '../ReviewAnswers.svelte';
  import ClipPlayer from './ClipPlayer.svelte';

  let {
    item,
    given = undefined,
    onanswer,
  }: { item: ReviewItem; given?: string | null; onanswer: (text: string, answer: string | null) => void } = $props();

  let busy = $state(false);
  const sounds = $derived(item.sensor.sounds ?? []);
  const asked = $derived(sounds.find((s) => s.key === item.state_key)?.name ?? item.state_key);
  const own = $derived(sounds.filter((s) => s.own && s.key !== item.state_key));

  async function answer(action: Answer, key?: string, newName?: string) {
    if (busy) return;
    busy = true;
    try {
      await api.answerSound(item.id, action === 'pick' ? 'sound' : action, key, newName);
      const text =
        action === 'yes'
          ? `✓ ${asked}`
          : action === 'no'
            ? `✓ Not ${asked}`
            : action === 'pick'
              ? `✓ ${sounds.find((s) => s.key === key)?.name ?? key}`
              : action === 'new'
                ? `✓ New: ${newName}`
                : 'Skipped';
      onanswer(text, action === 'skip' ? null : action === 'pick' ? (key ?? null) : action);
    } catch (err) {
      toastError(err);
    } finally {
      busy = false;
    }
  }
</script>

<div class="col">
  <!-- Not keyed: once a clip is played, the next question's plays at once, over and over (ClipPlayer). -->
  <ClipPlayer src={api.historyClipUrl(item.id)} video={item.clip_video} loop />
  {#key item.id}
  <ReviewAnswers
    {asked}
    choices={own}
    noun="sound"
    noLabel="No, something else"
    footnote="Your answer teaches the sound with this clip: yes, it was; no, it was not (see the sensor's Dataset tab)."
    {given}
    {busy}
    onanswer={answer}
  >
    {#snippet question()}
      <span>Was this <strong>{asked}</strong>?</span>
      {#if item.review_reason === 'near'}
        <span class="small muted">It scored {pct(item.confidence)} at most — almost, not quite enough to be heard.</span>
      {:else}
        <span class="small muted">It scored {pct(item.confidence)} at most — only just enough to be heard.</span>
      {/if}
    {/snippet}
  </ReviewAnswers>
  {/key}
</div>
