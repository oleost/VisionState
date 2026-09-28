<script lang="ts">
  import { dismiss, toasts } from '../app.svelte';
  import Icon from './Icon.svelte';
</script>

<div class="toasts" role="status" aria-live="polite">
  {#each toasts as t (t.id)}
    <div class="toast {t.tone}">
      <Icon name={t.tone === 'ok' ? 'check' : 'alert'} />
      <span class="msg">{t.message}</span>
      {#if t.action}
        <button
          class="btn sm accent-outline"
          onclick={() => {
            t.action?.run();
            dismiss(t.id);
          }}>{t.action.label}</button
        >
      {/if}
      <button class="close" aria-label="Dismiss" onclick={() => dismiss(t.id)}><Icon name="x" size={14} /></button>
    </div>
  {/each}
</div>

<style>
  .toasts {
    position: fixed;
    bottom: var(--space-5);
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    z-index: 50;
    width: min(560px, calc(100vw - 32px));
  }
  .toast {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: 10px 12px 10px 16px;
    border-radius: var(--radius-lg);
    background: var(--c-surface-2);
    border: 1px solid var(--c-border-strong);
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
    font-size: var(--fs-base);
  }
  .toast.ok {
    border-color: var(--c-accent-line);
    color: var(--c-text);
  }
  .toast.ok :global(svg) {
    color: var(--c-accent);
  }
  .toast.warn {
    border-color: var(--c-warn-line);
  }
  .toast.danger {
    border-color: var(--c-danger-line);
    color: var(--c-danger-text);
  }
  .msg {
    flex-grow: 1;
  }
  .close {
    background: none;
    border: none;
    color: var(--c-muted);
    cursor: pointer;
    padding: 4px;
    display: flex;
  }
</style>
