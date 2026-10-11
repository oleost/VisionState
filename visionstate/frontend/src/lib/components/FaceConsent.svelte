<script lang="ts">
  // Before face recognition is turned on for a sensor: what it does, what is stored and what is
  // downloaded, said plainly. Nothing is downloaded until the user confirms. The model's name,
  // licence, size and source come from the backend (GET /settings). A bottom sheet on phones.
  import { onMount } from 'svelte';
  import { size } from '../format';
  import type { FaceModelInfo } from '../types';
  import Icon from './Icon.svelte';

  let {
    name,
    model,
    onclose,
    onconfirm,
  }: {
    /** The sensor's name ("" while it is being made). */
    name: string;
    model: FaceModelInfo | null;
    onclose: () => void;
    onconfirm: () => void;
  } = $props();

  let panel: HTMLDivElement;
  onMount(() => panel?.querySelector<HTMLElement>('button.first')?.focus());

  function keydown(e: KeyboardEvent) {
    if (e.key === 'Escape') onclose();
  }
</script>

<svelte:window onkeydown={keydown} />

<div class="backdrop" onclick={onclose} role="presentation"></div>
<div class="sheet" role="dialog" aria-modal="true" aria-label="Recognise faces" bind:this={panel}>
  <div class="row head">
    <strong>Recognise faces{name ? ` on ${name}` : ''}?</strong>
    <span class="spacer"></span>
    <button class="btn ghost icon close" onclick={onclose} aria-label="Close"><Icon name="x" /></button>
  </div>
  <ul class="small">
    <li>
      Faces are looked for only in the people this sensor finds, and compared with faces <strong>you</strong> name —
      the people are shared: those you named on other cameras are known here too.
    </li>
    <li>
      Stored on this Home Assistant only, in the app's own storage (not the media folder, and in its backups): a small
      picture of each face you teach. Faces of people you have not named are not kept, apart from the normal history
      frames. The people you teach should know about it.
    </li>
    {#if model}
      <li>
        Needs the face model <em>{model.name}</em> with {model.detector.name} ({model.license} and {model.detector.license}{model.installed
          ? ', already downloaded'
          : `, ${size(model.size)} downloaded once from Hugging Face`}). {model.data} {model.detector.data}
      </li>
    {/if}
    <li>Cameras that see a street or a neighbour's door may need their consent where you live.</li>
    <li>It is not a lock: a photo of a face looks like that face. Do not unlock doors or disarm alarms on it alone.</li>
  </ul>
  <span class="xsmall faint">More in the app's Documentation tab.</span>
  <div class="row actions">
    <button class="btn primary first" onclick={onconfirm}>{model?.installed ? 'Turn on' : 'Download and turn on'}</button>
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
