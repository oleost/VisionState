<script lang="ts">
  // A sound sensor's ESP32 of ours (or another ESPHome device with our stream): its microphone's
  // controls, set through Home Assistant, and a word when a newer firmware is out. Shown only when
  // Home Assistant has a device at the sensor's address.
  import { onDestroy } from 'svelte';
  import { toastError } from '../../app.svelte';
  import { api } from '../../api';
  import { pollWhileVisible } from '../../poll';
  import { POLL } from '../../ui';
  import type { SensorDevice } from '../../types';

  let { sensorId }: { sensorId: number } = $props();

  let device = $state<SensorDevice | null>(null);
  let busy = $state<string | null>(null);
  // A gain being moved: shown as it is dragged, sent when it is let go.
  let dragging = $state<Record<string, number>>({});

  async function load() {
    try {
      device = (await api.sensorDevice(sensorId)).device;
    } catch {
      device = null; // Home Assistant not reachable: no controls, nothing to report here
    }
  }

  load();
  onDestroy(pollWhileVisible(POLL.device, load));

  async function set(entityId: string, value: boolean | number) {
    busy = entityId;
    try {
      await api.setSensorDevice(sensorId, entityId, value);
      await load();
    } catch (err) {
      toastError(err);
    } finally {
      busy = null;
      delete dragging[entityId];
    }
  }
</script>

{#if device && (device.controls.length || device.outdated)}
  <section class="card pad col device" data-testid="sound-device">
    <div class="card-title">
      <h3>The microphone</h3>
      <span class="xsmall faint">{device.name} · {device.address}</span>
    </div>
    {#if device.outdated}
      <div class="notice warn small" data-testid="firmware-outdated">
        <span>
          A newer VisionState firmware is out ({device.latest}; this device has {device.firmware}). In the ESPHome app,
          choose <b>Install</b> for the device: its file follows the firmware's line, so nothing in it needs changing.
        </span>
      </div>
    {/if}
    {#each device.controls as c (c.entity_id)}
      {#if c.kind === 'switch'}
        <label class="check small">
          <input
            type="checkbox"
            checked={c.state === 'on'}
            disabled={busy === c.entity_id || c.state === 'unavailable'}
            onchange={(e) => set(c.entity_id, (e.currentTarget as HTMLInputElement).checked)}
          />
          <span>{c.name}</span>
        </label>
      {:else}
        {@const value = dragging[c.entity_id] ?? Number(c.state)}
        <label class="field small">
          <span class="row gain"><span>{c.name}</span><span class="spacer"></span><b>{Number.isFinite(value) ? value : '–'}</b></span>
          <input
            type="range"
            min={c.min ?? 1}
            max={c.max ?? 64}
            step={c.step ?? 1}
            value={Number.isFinite(value) ? value : (c.min ?? 1)}
            disabled={busy === c.entity_id || c.state === 'unavailable'}
            aria-label={c.name}
            oninput={(e) => (dragging[c.entity_id] = Number((e.currentTarget as HTMLInputElement).value))}
            onchange={(e) => set(c.entity_id, Number((e.currentTarget as HTMLInputElement).value))}
          />
        </label>
      {/if}
    {/each}
    {#if device.controls.length}
      <span class="xsmall faint">Set on the device through Home Assistant. Watch the level above while you change the gain.</span>
    {/if}
  </section>
{/if}

<style>
  .device {
    gap: var(--space-2);
  }
  .gain {
    align-items: baseline;
  }
  input[type='range'] {
    width: 100%;
  }
</style>
