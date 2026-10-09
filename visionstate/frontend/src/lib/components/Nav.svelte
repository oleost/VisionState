<script lang="ts">
  import { app } from '../app.svelte';
  import { href, paths, route } from '../router.svelte';
  import { BETA_VERSION_PATTERN } from '../ui';
  import Logo from './Logo.svelte';

  const section = $derived(['review', 'history', 'settings'].includes(route.parts[0]) ? route.parts[0] : 'sensors');
  const items = $derived([
    { id: 'sensors', label: 'Sensors', path: paths.dashboard(), badge: 0 },
    { id: 'review', label: 'Review', path: paths.review(), badge: app.status?.review_count ?? 0 },
    { id: 'history', label: 'History', path: paths.history(), badge: 0 },
    { id: 'settings', label: 'Settings', path: paths.settings(), badge: 0 },
  ]);
</script>

<nav aria-label="Main">
  <a class="brand" href={href(paths.dashboard())}>
    <Logo />
    <span>VisionState</span>
    {#if app.status && BETA_VERSION_PATTERN.test(app.status.version)}
      <span class="beta" title="Beta version {app.status.version}">BETA</span>
    {/if}
  </a>
  <div class="links">
    {#each items as item (item.id)}
      <a href={href(item.path)} class:active={section === item.id} aria-current={section === item.id ? 'page' : undefined}>
        {item.label}
        {#if item.badge}<span class="badge">{item.badge}</span>{/if}
      </a>
    {/each}
  </div>
  <div class="spacer"></div>
  {#if app.status}
    <div class="status">
      <span class="row" title={app.status.mqtt.error || app.status.mqtt.host || ''}>
        <span class="dot" style:background={app.status.mqtt.connected ? 'var(--c-accent)' : 'var(--c-danger)'}></span>
        MQTT {app.status.mqtt.connected ? 'connected' : 'offline'}
      </span>
    </div>
  {/if}
</nav>

<style>
  nav {
    height: var(--nav-h);
    padding: 0 var(--page-pad);
    display: flex;
    align-items: center;
    gap: var(--space-8);
    background: var(--c-nav);
    border-bottom: 1px solid var(--c-border);
    position: sticky;
    top: 0;
    z-index: 10;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
    color: var(--c-text);
    font: 600 var(--fs-xl) var(--font-display);
  }
  .beta {
    font: 600 var(--fs-xs) var(--font-body);
    letter-spacing: 0.08em;
    padding: 2px 7px;
    border-radius: var(--radius-pill);
    background: var(--c-badge);
    color: var(--c-badge-ink);
  }
  .links {
    display: flex;
    gap: var(--space-1);
  }
  .links a {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    height: 36px;
    padding: 0 14px;
    border-radius: var(--radius-md);
    color: var(--c-muted);
    font-weight: 500;
  }
  .links a.active,
  .links a:hover {
    color: var(--c-text);
    background: var(--c-surface-3);
  }
  .badge {
    min-width: 20px;
    height: 20px;
    padding: 0 6px;
    border-radius: var(--radius-pill);
    background: var(--c-badge);
    color: var(--c-badge-ink);
    font-size: var(--fs-sm);
    font-weight: 600;
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }
  .status {
    display: flex;
    align-items: center;
    gap: var(--space-4);
    font-size: var(--fs-md);
    color: var(--c-muted);
  }
  @media (max-width: 900px) {
    .status {
      display: none;
    }
    nav {
      gap: var(--space-4);
    }
    .brand span {
      display: none;
    }
  }
  @media (max-width: 600px) {
    nav {
      gap: var(--space-3);
    }
    /* Four sections on a phone: tighter. If they still do not fit (a very narrow screen, a large
       review count), the links scroll instead of widening the page. */
    .links {
      gap: 0;
      min-width: 0;
      overflow-x: auto;
      scrollbar-width: none;
    }
    .links a {
      gap: var(--space-1);
      padding: 0 8px;
      white-space: nowrap;
    }
  }
  @media (max-width: 440px) {
    /* The logo goes on phones: Sensors leads to the same page. */
    .brand {
      display: none;
    }
    .links {
      margin-left: -8px; /* the first link's text lines up with the page */
    }
  }
</style>
