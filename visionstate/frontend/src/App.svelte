<script lang="ts">
  import { onDestroy } from 'svelte';
  import { app, loadConfig, refreshStatus } from './lib/app.svelte';
  import Nav from './lib/components/Nav.svelte';
  import Toasts from './lib/components/Toasts.svelte';
  import { route } from './lib/router.svelte';
  import { POLL, SENSOR_TABS, type SensorTab } from './lib/ui';
  import Dashboard from './pages/Dashboard.svelte';
  import History from './pages/History.svelte';
  import NewSensor from './pages/NewSensor.svelte';
  import Review from './pages/Review.svelte';
  import SensorPage from './pages/SensorPage.svelte';
  import Settings from './pages/Settings.svelte';

  let ready = $state(false);

  Promise.all([loadConfig(), refreshStatus()])
    .then(() => (ready = true))
    .catch((err) => (app.error = err.message));
  const timer = setInterval(refreshStatus, POLL.status);
  onDestroy(() => clearInterval(timer));

  const [section, id, tab] = $derived(route.parts);
  // '' = the sensor's first tab, which depends on its kind (resolved by SensorPage).
  const sensorTab = $derived((SENSOR_TABS.some((t) => t.id === tab) ? tab : '') as SensorTab | '');
</script>

<Nav />
<main>
  {#if !ready}
    <div class="page">
      {#if app.error}<div class="notice danger">Could not reach the VisionState backend: {app.error}</div>{:else}<p class="muted">Loading…</p>{/if}
    </div>
  {:else if section === 'sensors' && id === 'new'}
    <NewSensor />
  {:else if section === 'sensors' && id}
    <SensorPage id={Number(id)} tab={sensorTab} />
  {:else if section === 'review'}
    <Review />
  {:else if section === 'history'}
    <History />
  {:else if section === 'settings'}
    <Settings />
  {:else}
    <Dashboard />
  {/if}
</main>
<Toasts />
