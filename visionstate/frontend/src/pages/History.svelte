<script lang="ts">
  // Everything the sensors recorded, filtered by sensor, what was seen, event and time.
  import { api } from '../lib/api';
  import { toastError } from '../lib/app.svelte';
  import HistoryView from '../lib/components/history/HistoryView.svelte';
  import type { Sensor } from '../lib/types';

  let sensors = $state<Sensor[] | null>(null);

  // Rows need their sensor (names, states, own labels); a box corrected on a row changes it.
  async function load() {
    try {
      sensors = await api.sensors();
    } catch (err) {
      toastError(err);
    }
  }
  load();
</script>

<div class="page">
  <h1>History</h1>
  {#if sensors === null}
    <p class="muted">Loading…</p>
  {:else}
    <HistoryView
      {sensors}
      intro="What every sensor recorded: state changes, objects that came and went, readings, and frames sent to review."
      onchange={load}
    />
  {/if}
</div>
