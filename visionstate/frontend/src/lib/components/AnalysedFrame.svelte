<script lang="ts">
  // The exact frame an object sensor's last check analysed, with its detections drawn on it,
  // so boxes always match the picture. Falls back to the latest cached frame (without boxes)
  // when that frame is no longer kept by the backend.
  import { onDestroy } from 'svelte';
  import { api } from '../api';
  import type { Sensor } from '../types';
  import DetectionBoxes from './DetectionBoxes.svelte';
  import RoiEditor from './RoiEditor.svelte';

  let { sensor, labels = true }: { sensor: Sensor; labels?: boolean } = $props();

  let src = $state<string | null>(null);
  let shownFrame = $state<string | null>(null); // frame id of the picture on screen (null = fallback)
  let objectUrl: string | null = null;

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
    if (frameId === shownFrame) return;
    const url = api.analysedFrameUrl(sensor.id, frameId);
    const img = new Image();
    img.onload = () => {
      src = url;
      shownFrame = frameId;
    };
    img.onerror = () => {
      if (!src) fallback();
    };
    img.src = url;
  });

  onDestroy(() => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
  });

  // Boxes belong to the analysed frame only.
  const detections = $derived(shownFrame && shownFrame === sensor.live.frame_id ? (sensor.objects?.detections ?? []) : []);
</script>

<RoiEditor {src} roi={sensor.roi}>
  <DetectionBoxes {detections} classes={sensor.objects?.classes ?? []} {labels} />
</RoiEditor>
