<script lang="ts">
  import { onDestroy } from 'svelte';
  import { app, loadConfig, refreshStatus } from './lib/app.svelte';
  import Nav from './lib/components/Nav.svelte';
  import Toasts from './lib/components/Toasts.svelte';
  import { pollWhileVisible } from './lib/poll';
  import { href, paths, route } from './lib/router.svelte';
  import { OLD_TABS, POLL, SENSOR_TABS, SETTINGS_SECTIONS, type SensorTab, type SettingsSection } from './lib/ui';
  import Dashboard from './pages/Dashboard.svelte';
  import History from './pages/History.svelte';
  import NewSensor from './pages/NewSensor.svelte';
  import People from './pages/People.svelte';
  import Review from './pages/Review.svelte';
  import SensorPage from './pages/SensorPage.svelte';
  import Settings from './pages/Settings.svelte';
  import SpeedTest from './pages/SpeedTest.svelte';

  let ready = $state(false);

  Promise.all([loadConfig(), refreshStatus()])
    .then(() => (ready = true))
    .catch((err) => (app.error = err.message));
  onDestroy(pollWhileVisible(POLL.status, refreshStatus));

  const [section, id, tab, part] = $derived(route.parts);
  // '' = the sensor's first tab, which depends on its kind (resolved by SensorPage). A tab of an
  // earlier version opens where it went.
  const sensorTab = $derived(
    (OLD_TABS[tab]?.tab ?? (SENSOR_TABS.some((t) => t.id === tab) ? tab : '')) as SensorTab | '',
  );
  const settingsSection = $derived(
    (OLD_TABS[tab]?.section ?? (SETTINGS_SECTIONS.includes(part as SettingsSection) ? part : '')) as SettingsSection | '',
  );
</script>

<Nav />
<main>
  {#if app.status?.benchmark_running && section !== 'speed-test'}
    <div class="notice warn speed-test-running">
      A speed test is running: all sensors are paused. <a href={href(paths.speedTest())}>See how it goes</a>
    </div>
  {/if}
  {#if !ready}
    <div class="page">
      {#if app.error}<div class="notice danger">Could not reach the VisionState backend: {app.error}</div>{:else}<p class="muted">Loading…</p>{/if}
    </div>
  {:else if section === 'sensors' && id === 'new'}
    <NewSensor />
  {:else if section === 'sensors' && id}
    <SensorPage id={Number(id)} tab={sensorTab} section={settingsSection} />
  {:else if section === 'review'}
    <Review />
  {:else if section === 'history'}
    <History />
  {:else if section === 'people'}
    <People />
  {:else if section === 'speed-test'}
    <SpeedTest />
  {:else if section === 'settings'}
    {#key app.restored}<Settings />{/key}
  {:else}
    <Dashboard />
  {/if}
</main>
<Toasts />
