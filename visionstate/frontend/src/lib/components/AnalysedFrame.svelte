<script lang="ts">
  // The exact frame an object sensor's last check analysed, with its detections drawn on it,
  // so boxes always match the picture. When that frame is no longer kept, the backend sends the
  // latest one (X-Frame-Id tells which), shown without boxes unless they belong to it.
  import { onDestroy } from 'svelte';
  import { api } from '../api';
  import type { Sensor } from '../types';
  import DetectionBoxes from './DetectionBoxes.svelte';
  import RoiEditor from './RoiEditor.svelte';

  let { sensor, labels = true }: { sensor: Sensor; labels?: boolean } = $props();

  let src = $state<string | null>(null);
  let shownFrame = $state<string | null>(null); // frame id of the picture on screen (null = fallback)
  let objectUrl: string | null = null;
  let requested = ''; // the frame asked for last (not reactive: the answer may be another frame)

  async function fallback() {
    try {
      const frame = await api.frame(sensor.id, true);
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = src = frame.url;
      shownFrame = null;
    } catch {
      /* camera unavailable: keep what is shown */
    }
  }

  $effect(() => {
    const frameId = sensor.live.frame_id;
    if (!frameId) {
      if (!src) fallback();
      return;
    }
    if (frameId === requested) return;
    requested = frameId;
    load(frameId);
  });

  async function load(frameId: string) {
    try {
      const resp = await fetch(api.analysedFrameUrl(sensor.id, frameId));
      if (!resp.ok) throw new Error(String(resp.status));
      const blob = await resp.blob();
      if (frameId !== requested) return; // a newer check's frame is on its way
      const url = URL.createObjectURL(blob);
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = src = url;
      shownFrame = resp.headers.get('X-Frame-Id');
    } catch {
      if (!src) fallback();
    }
  }

  onDestroy(() => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
  });

  // Boxes belong to the analysed frame only.
  const detections = $derived(shownFrame && shownFrame === sensor.live.frame_id ? (sensor.objects?.detections ?? []) : []);
</script>

<RoiEditor {src} roi={sensor.roi}>
  <DetectionBoxes {detections} classes={sensor.objects?.classes ?? []} {labels} />
</RoiEditor>
