<script lang="ts">
  // The exact frame an object sensor's last check analysed, with its detections drawn on it,
  // so boxes always match the picture. When that frame is no longer kept, the backend sends the
  // latest one (X-Frame-Id tells which), shown without boxes unless they belong to it.
  // With `teach`, boxes can be corrected (TeachableFrame); the frame then stays put while a box
  // is being taught.
  import { onDestroy } from 'svelte';
  import { api } from '../api';
  import type { Detection, Sensor } from '../types';
  import DetectionBoxes from './DetectionBoxes.svelte';
  import RoiEditor from './RoiEditor.svelte';
  import TeachableFrame from './TeachableFrame.svelte';
  import { objectKeys } from '../objects';

  let {
    sensor,
    labels = true,
    teach = false,
    onchange = () => {},
  }: { sensor: Sensor; labels?: boolean; teach?: boolean; onchange?: () => void } = $props();

  let src = $state<string | null>(null);
  let shownFrame = $state<string | null>(null); // frame id of the picture on screen (null = fallback)
  let fallbackFrame = $state<string | null>(null); // frame id of a fallback picture (no boxes)
  let busy = $state(false); // a box on it is being taught: keep this frame
  let objectUrl: string | null = null;
  let requested = ''; // the frame asked for last (not reactive: the answer may be another frame)

  async function fallback() {
    try {
      const frame = await api.frame(sensor.id, true);
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = src = frame.url;
      shownFrame = null;
      fallbackFrame = frame.frameId;
    } catch {
      /* camera unavailable: keep what is shown */
    }
  }

  $effect(() => {
    const frameId = sensor.live.frame_id;
    if (busy) return;
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
      if (frameId !== requested || busy) return; // a newer check's frame is on its way, or a box is being taught
      const url = URL.createObjectURL(blob);
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = src = url;
      shownFrame = resp.headers.get('X-Frame-Id');
      fallbackFrame = null;
    } catch {
      if (!src) fallback();
    }
  }

  onDestroy(() => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
  });

  // Boxes belong to the analysed frame only; they stay as they were while a box is being taught.
  let detections = $state<Detection[]>([]);
  let boxesFrame = $state<string | null>(null);
  $effect(() => {
    if (busy) return;
    const matches = !!shownFrame && shownFrame === sensor.live.frame_id;
    detections = matches ? (sensor.objects?.detections ?? []) : [];
    boxesFrame = matches ? shownFrame : null;
  });
  const teachOn = $derived(boxesFrame ?? shownFrame ?? fallbackFrame);
</script>

{#if teach}
  <TeachableFrame {sensor} {src} {detections} source={teachOn ? { frame_id: teachOn } : null} bind:busy {onchange} />
{:else}
  <RoiEditor {src} roi={sensor.roi}>
    <DetectionBoxes {detections} classes={objectKeys(sensor)} own={sensor.objects?.custom ?? []} {labels} />
  </RoiEditor>
{/if}
