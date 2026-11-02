<script lang="ts">
  // Wizard, first step: a small picture from the chosen camera, so a wrong camera, one that does
  // not answer or a low-resolution stream shows where it is picked. The region is drawn in the next step.
  import Icon from '../../lib/components/Icon.svelte';

  let {
    url,
    size,
    busy,
    error,
    low,
    onretry,
  }: { url: string | null; size: [number, number] | null; busy: boolean; error: string; low: boolean; onretry: () => void } = $props();
</script>

<div class="col preview" data-testid="camera-preview">
  {#if error}
    <div class="notice danger"><Icon name="alert" />{error}</div>
  {/if}
  {#if low && size}
    <div class="notice warn small" data-testid="low-stream">
      <Icon name="alert" />
      <span>
        This picture is {size[0]}×{size[1]}. If the camera has a higher resolution, this may be its
        low-resolution stream: pick its main stream instead, which Home Assistant often hides (a disabled camera
        entity of the same device).
      </span>
    </div>
  {/if}
  {#if url}
    <img src={url} alt="What the camera sees" />
  {:else if busy}
    <div class="empty small muted">Getting a picture…</div>
  {/if}
  {#if url || error}
    <div class="row"><button type="button" class="btn sm" disabled={busy} onclick={onretry}><Icon name="refresh" size={14} /> New frame</button></div>
  {/if}
</div>

<style>
  .preview {
    gap: var(--space-2);
    max-width: 640px;
  }
  img,
  .empty {
    display: block;
    width: 100%;
    max-width: 320px;
    border-radius: var(--radius-md);
    border: 1px solid var(--c-border);
  }
  .empty {
    display: flex;
    align-items: center;
    justify-content: center;
    aspect-ratio: 16 / 9;
    background: var(--c-sunken);
  }
</style>
