<script lang="ts">
  // A view with live frames holds the sensor's light on while it is open (see backend lights.py):
  // it renews a lease every light_view.renew_s and lets go when it closes, is hidden or switched
  // off here. `ready` turns true once the light had time to get bright, so frames can be taken.
  import { onDestroy } from 'svelte';
  import { api } from '../api';
  import { app } from '../app.svelte';
  import { LIGHT_HOLD_STORAGE_KEY } from '../ui';
  import Icon from './Icon.svelte';

  let {
    entity,
    delay,
    ready = $bindable(false),
  }: { entity: string; delay: number; ready?: boolean } = $props();

  // One holder per open view; crypto.randomUUID needs HTTPS, which Home Assistant often is not.
  const holder = `view-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  let enabled = $state(remembered());
  let error = $state('');
  let timer: ReturnType<typeof setTimeout> | undefined;
  let held = ''; // the entity this view holds right now ("" = none)

  /** The switch is a per-viewer convenience: remembered in the browser, if it may store anything. */
  function remembered(): boolean {
    try {
      return localStorage.getItem(LIGHT_HOLD_STORAGE_KEY) !== 'off';
    } catch {
      return true;
    }
  }
  function remember(on: boolean) {
    try {
      localStorage.setItem(LIGHT_HOLD_STORAGE_KEY, on ? 'on' : 'off');
    } catch {
      /* private window or blocked storage: the switch still works for this view */
    }
  }

  async function renew() {
    clearTimeout(timer);
    if (!enabled || !entity || document.visibilityState !== 'visible') return letGo();
    try {
      const result = await api.holdLight(entity, holder, delay);
      held = entity;
      error = result.error;
      const warming = result.on && (result.wait_s ?? 0) > 0;
      // Check again when it should be bright, then keep renewing the lease.
      const next = warming ? (result.wait_s ?? 0) * 1000 + 100 : (app.config?.light_view.renew_s ?? 10) * 1000;
      ready = result.on && !warming;
      timer = setTimeout(renew, next);
    } catch (err) {
      error = (err as Error).message;
      ready = false;
      timer = setTimeout(renew, (app.config?.light_view.renew_s ?? 10) * 1000);
    }
  }

  function letGo() {
    clearTimeout(timer);
    ready = false;
    if (held) api.holdLight(held, holder, delay, false).catch(() => undefined); // the lease runs out anyway
    held = '';
  }

  $effect(() => {
    void entity;
    void enabled;
    if (held && held !== entity) letGo(); // another light was picked
    renew();
  });

  function toggle(on: boolean) {
    enabled = on;
    remember(on);
  }

  onDestroy(letGo);
</script>

<svelte:document onvisibilitychange={renew} />

<div class="row wrap light" class:off={!enabled} data-testid="light-hold">
  <span class="row msg">
    <Icon name="bulb" size={15} />
    <span class="small text">
      {#if !enabled}
        <span class="mono">{entity}</span> stays off while you look
      {:else if error}
        <span class="warn-text">Could not switch on <span class="mono">{entity}</span>: {error}</span>
      {:else if ready}
        <span class="mono">{entity}</span> is on while this view is open
      {:else}
        Switching on <span class="mono">{entity}</span>…
      {/if}
    </span>
  </span>
  <label class="row switch small">
    <input type="checkbox" checked={enabled} onchange={(e) => toggle(e.currentTarget.checked)} aria-label="Light on while this view is open" />
    Light
  </label>
</div>

<style>
  .light {
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    border-radius: var(--radius-md);
    background: var(--c-surface-2);
    border: 1px solid var(--c-border);
    color: var(--c-warn);
  }
  .light.off {
    color: var(--c-muted);
  }
  /* The bulb stays with the text; the switch goes to its own line on a narrow screen. */
  .msg {
    flex: 1 1 240px;
    gap: var(--space-2);
    align-items: flex-start;
    min-width: 0;
  }
  .msg :global(svg) {
    flex-shrink: 0;
    margin-top: 2px;
  }
  .text {
    color: var(--c-text-2);
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .warn-text {
    color: var(--c-warn-text);
  }
  .switch {
    margin-left: auto;
    gap: 6px;
    color: var(--c-text-2);
    cursor: pointer;
  }
</style>
