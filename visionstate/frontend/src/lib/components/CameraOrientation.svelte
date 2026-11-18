<script lang="ts">
  // An ESP32 camera of ours: which way up its picture is (Picture orientation, kept on the device
  // and set through Home Assistant at once, not with the sensor's settings), shown when Home
  // Assistant has the device at the sensor's address and it has the choice; and a word when a newer
  // firmware is out (the choice came with one).
  import { onDestroy } from 'svelte';
  import { toastError } from '../app.svelte';
  import { api } from '../api';
  import { pollWhileVisible } from '../poll';
  import { POLL } from '../ui';
  import type { SensorDevice } from '../types';

  let { sensorId }: { sensorId: number } = $props();

  let device = $state<SensorDevice | null>(null);
  let busy = $state(false);
  const control = $derived(device?.controls.find((c) => c.kind === 'select') ?? null);

  async function load() {
    try {
      device = (await api.sensorDevice(sensorId)).device;
    } catch {
      device = null; // Home Assistant not reachable: no choice to offer
    }
  }

  load();
  onDestroy(pollWhileVisible(POLL.device, load));

  async function choose(value: string) {
    if (!control) return;
    busy = true;
    try {
      await api.setSensorDevice(sensorId, control.entity_id, value);
      await load();
    } catch (err) {
      toastError(err);
    } finally {
      busy = false;
    }
  }
</script>

{#if device?.outdated}
  <div class="notice warn small" data-testid="camera-firmware-outdated">
    <span>
      A newer VisionState firmware is out ({device.latest}; this camera has {device.firmware}). In the ESPHome app,
      choose <b>Install</b> for {device.name}: its file follows the firmware's line, so nothing in it needs changing.
    </span>
  </div>
{/if}
{#if control}
  <label class="field" data-testid="camera-orientation">
    {control.name}
    <select
      class="input"
      value={control.state}
      disabled={busy || control.state === 'unavailable'}
      aria-label={control.name}
      onchange={(e) => choose((e.currentTarget as HTMLSelectElement).value)}
    >
      {#each control.options ?? [] as option (option)}<option value={option}>{option}</option>{/each}
    </select>
    <span class="xsmall muted">
      Turns or mirrors the picture on the camera itself, for every sensor on it, at once (no need to save). Any other
      angle: straighten it in the region.
    </span>
  </label>
{/if}
