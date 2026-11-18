<script lang="ts">
  // Before a feature that keeps or reads something about people is turned on (faces, licence
  // plates): what it does, what is stored and what is downloaded, said plainly. Nothing happens
  // until the user confirms. A centred dialog, a bottom sheet on phones.
  import { onMount, type Snippet } from 'svelte';
  import Icon from './Icon.svelte';

  let {
    title,
    confirm,
    onclose,
    onconfirm,
    children,
  }: { title: string; confirm: string; onclose: () => void; onconfirm: () => void; children: Snippet } = $props();

  let panel: HTMLDivElement;
  onMount(() => panel?.querySelector<HTMLElement>('button.first')?.focus());

  function keydown(e: KeyboardEvent) {
    if (e.key === 'Escape') onclose();
  }
</script>

<svelte:window onkeydown={keydown} />

<div class="backdrop" onclick={onclose} role="presentation"></div>
<div class="sheet" role="dialog" aria-modal="true" aria-label={title} bind:this={panel}>
  <div class="row head">
    <strong>{title}</strong>
    <span class="spacer"></span>
    <button class="btn ghost icon close" onclick={onclose} aria-label="Close"><Icon name="x" /></button>
  </div>
  <ul class="small">
    {@render children()}
  </ul>
  <span class="xsmall faint">More in the app's Documentation tab.</span>
  <div class="row actions">
    <button class="btn primary first" onclick={onconfirm}>{confirm}</button>
    <button class="btn" onclick={onclose}>Cancel</button>
  </div>
</div>

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    z-index: 40;
    background: rgba(5, 7, 9, 0.6);
  }
  .sheet {
    position: fixed;
    z-index: 41;
    left: 50%;
    top: 50%;
    transform: translate(-50%, -50%);
    width: min(480px, calc(100vw - 32px));
    max-height: calc(100vh - 48px);
    overflow-y: auto;
    background: var(--c-surface);
    border: 1px solid var(--c-border-strong);
    border-radius: var(--radius-lg);
    padding: var(--space-4);
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
  }
  @media (max-width: 600px) {
    .sheet {
      left: 0;
      right: 0;
      top: auto;
      bottom: 0;
      transform: none;
      width: auto;
      max-height: 85vh;
      border-radius: var(--radius-lg) var(--radius-lg) 0 0;
      padding-bottom: calc(var(--space-4) + env(safe-area-inset-bottom));
    }
  }
  .head {
    align-items: flex-start;
    gap: var(--space-3);
  }
  .close {
    flex-shrink: 0;
    margin: -6px -6px 0 0;
  }
  ul {
    margin: 0;
    padding-left: 1.2em;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    color: var(--c-text-2);
  }
  .actions {
    gap: var(--space-2);
    margin-top: var(--space-2);
  }
</style>
