<script lang="ts">
  // "Was this a siren?" — a sound that was only just heard. Listen to its clip (watch it, on a
  // camera) and answer: yes, no, it was one of the sensor's own sounds, or a new one. The answer is
  // kept, and teaches the clip where it can (an own sound, or "other" for the own sounds).
  import { api } from '../../api';
  import { toastError } from '../../app.svelte';
  import { pct } from '../../format';
  import type { ReviewItem } from '../../types';
  import Icon from '../Icon.svelte';
  import ClipPlayer from './ClipPlayer.svelte';

  let {
    item,
    given = undefined,
    onanswer,
  }: { item: ReviewItem; given?: string | null; onanswer: (text: string, answer: string | null) => void } = $props();

  let busy = $state(false);
  let name = $state('');
  const sounds = $derived(item.sensor.sounds ?? []);
  const asked = $derived(sounds.find((s) => s.key === item.state_key)?.name ?? item.state_key);
  const own = $derived(sounds.filter((s) => s.own && s.key !== item.state_key));

  async function answer(action: 'yes' | 'no' | 'sound' | 'new' | 'skip', key?: string, newName?: string) {
    if (busy) return;
    busy = true;
    try {
      await api.answerSound(item.id, action, key, newName);
      const text =
        action === 'yes'
          ? `✓ ${asked}`
          : action === 'no'
            ? `✓ Not ${asked}`
            : action === 'sound'
              ? `✓ ${sounds.find((s) => s.key === key)?.name ?? key}`
              : action === 'new'
                ? `✓ New: ${newName}`
                : 'Skipped';
      onanswer(text, action === 'skip' ? null : action === 'sound' ? (key ?? null) : action);
    } catch (err) {
      toastError(err);
    } finally {
      busy = false;
    }
  }

  function onkey(e: KeyboardEvent) {
    if ((e.target as HTMLElement).closest('input, select, textarea, audio, video')) return;
    if (e.key === 'Enter') answer('yes');
    else if (e.key.toLowerCase() === 'n') answer('no');
    else if (e.key.toLowerCase() === 's') answer('skip');
  }
</script>

<svelte:window onkeydown={onkey} />

<div class="col question-wrap">
  {#key item.id}
    <ClipPlayer src={api.historyClipUrl(item.id)} video={item.clip_video} />
  {/key}
  <div class="col center">
    <p class="question">
      <span>Was this <strong>{asked}</strong>?</span>
      <span class="small muted">It scored {pct(item.confidence)} at most — only just enough to be heard.</span>
    </p>
    <div class="row wrap center-row">
      <button class="btn primary lg" class:chosen={given === 'yes'} disabled={busy} onclick={() => answer('yes')}>
        <Icon name="check" /> Yes, {asked}
      </button>
      <button class="btn lg" class:chosen={given === 'no'} disabled={busy} onclick={() => answer('no')}>No</button>
      <button class="btn lg ghost" class:chosen={given === null} disabled={busy} onclick={() => answer('skip')}>Skip</button>
    </div>
    {#if own.length}
      <div class="row wrap center-row">
        <span class="small faint">It was</span>
        {#each own as s (s.key)}
          <button class="btn" class:chosen={given === s.key} disabled={busy} onclick={() => answer('sound', s.key)}>{s.name}</button>
        {/each}
      </div>
    {/if}
    <form
      class="row wrap center-row"
      onsubmit={(e) => {
        e.preventDefault();
        if (name.trim()) answer('new', undefined, name.trim());
      }}
    >
      <input class="input" bind:value={name} placeholder="It was something else: name it" maxlength="64" aria-label="A new own sound" />
      <button class="btn" disabled={busy || !name.trim()}>Teach</button>
    </form>
    <span class="xsmall faint">Your answer is kept. An own sound learns from it; the built-in sounds do not change.</span>
    <span class="xsmall faint kbd-only">Enter = yes · N = no · S = skip</span>
  </div>
</div>

<style>
  .question-wrap {
    gap: var(--space-4);
  }
  .center {
    align-items: center;
    text-align: center;
    gap: var(--space-3);
  }
  .center-row {
    justify-content: center;
    gap: var(--space-2);
  }
  .question {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin: 0;
    font-size: var(--fs-lg);
  }
  .chosen {
    outline: 2px solid var(--c-accent);
  }
  form input {
    flex: 1 1 220px;
    max-width: 360px;
    min-width: 0;
  }
</style>
