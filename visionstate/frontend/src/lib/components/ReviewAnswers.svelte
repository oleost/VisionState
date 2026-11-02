<script lang="ts">
  // The answers to a review question about something the sensor was taught: "Yes, Kari", "No, it
  // is" one of the others, a new one (named here), "No" and "Skip", with their keys. A face and a
  // sound ask the same way (FaceQuestion, SoundQuestion); the question above it is theirs.
  import type { Snippet } from 'svelte';
  import Icon from './Icon.svelte';

  export type Answer = 'yes' | 'no' | 'skip' | 'pick' | 'new';

  let {
    asked,
    choices,
    noun,
    noLabel,
    skipLabel = 'Skip',
    footnote,
    given,
    busy,
    onanswer,
    question,
  }: {
    /** What the question names ("Kari", "Siren"); null when it asks "Who is this?". */
    asked: string | null;
    /** The others it can be. */
    choices: { key: string; name: string }[];
    /** What a new one is ("person", "sound"). */
    noun: string;
    /** "No" with what it then is ("No, someone I do not know"); none: no such answer. */
    noLabel: string | null;
    skipLabel?: string;
    footnote: string;
    /** The answer given before, when the item is opened again: an action, or the key picked. */
    given: string | null | undefined;
    busy: boolean;
    onanswer: (action: Answer, key?: string, name?: string) => void;
    question: Snippet;
  } = $props();

  let naming = $state(false);
  let newName = $state('');

  function keydown(e: KeyboardEvent) {
    if (naming || (e.target as HTMLElement).closest('input, select, textarea, audio, video')) return;
    if (e.key === 'Enter' && asked !== null) onanswer('yes');
    else if (e.key.toLowerCase() === 'n' && noLabel) onanswer('no');
    else if (e.key.toLowerCase() === 's') onanswer('skip');
  }
  const keys = $derived(
    [asked !== null && 'Enter = yes', noLabel && 'N = no', `S = ${skipLabel.toLowerCase()}`].filter(Boolean).join(' · '),
  );
</script>

<svelte:window onkeydown={keydown} />

<div class="col center">
  <p class="question">{@render question()}</p>
  {#if naming}
    <form
      class="row wrap center-row"
      onsubmit={(e) => {
        e.preventDefault();
        if (newName.trim()) onanswer('new', undefined, newName.trim());
      }}
    >
      <!-- svelte-ignore a11y_autofocus -->
      <input class="input" bind:value={newName} maxlength="64" placeholder="Name" aria-label="Name of the new {noun}" autofocus />
      <button class="btn primary" type="submit" disabled={busy || !newName.trim()}>Add {noun}</button>
      <button class="btn" type="button" onclick={() => (naming = false)}>Back</button>
    </form>
  {:else}
    <div class="row wrap center-row">
      {#if asked !== null}
        <button class="btn primary lg" class:chosen={given === 'yes'} disabled={busy} onclick={() => onanswer('yes')}>
          <Icon name="check" /> Yes, {asked}
        </button>
        <span class="small faint">No, it is</span>
      {/if}
      {#each choices as c (c.key)}
        <button class="btn lg" class:chosen={given === c.key} disabled={busy} onclick={() => onanswer('pick', c.key)}>{c.name}</button>
      {/each}
      <button class="btn lg accent-outline" disabled={busy} onclick={() => (naming = true)}>
        <Icon name="plus" size={14} /> New {noun}…
      </button>
    </div>
    <div class="row wrap center-row">
      {#if noLabel}
        <button class="btn ghost" class:chosen={given === 'no'} disabled={busy} onclick={() => onanswer('no')}>{noLabel}</button>
      {/if}
      <button class="btn ghost" class:chosen={given === null} disabled={busy} onclick={() => onanswer('skip')}>{skipLabel}</button>
    </div>
  {/if}
  <span class="xsmall faint">{footnote}</span>
  <span class="xsmall faint kbd-only">{keys}</span>
</div>

<style>
  .center {
    align-items: center;
    text-align: center;
    gap: var(--space-3);
    margin-top: var(--space-4);
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
</style>
