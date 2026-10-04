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

  // Boxes belong to the frame on screen: they are swapped together with the picture, once the
  // new frame has loaded — not when the next check is announced, which would empty the list
  // under the frame for a moment and make the page jump.
  let detections = $state<Detection[]>([]);
  let boxesFrame = $state<string | null>(null);

  $effect(() => {
    const frameId = sensor.live.frame_id;
    const found = sensor.objects?.detections ?? []; // the boxes of that check
    if (busy) return;
    if (!frameId) {
      if (!src) fallback();
      return;
    }
    if (frameId === requested) return;
    requested = frameId;
    load(frameId, found);
  });

  async function load(frameId: string, found: Detection[]) {
    try {
      const resp = await fetch(api.analysedFrameUrl(sensor.id, frameId));
      if (!resp.ok) throw new Error(String(resp.status));
      const blob = await resp.blob();
      if (frameId !== requested) return; // a newer check's frame is on its way
      if (busy) {
        requested = ''; // a box is being taught: load the newest frame once that is done
        return;
      }
      const url = URL.createObjectURL(blob);
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = src = url;
      shownFrame = resp.headers.get('X-Frame-Id');
      fallbackFrame = null;
      // The backend sends the latest frame instead when this one is gone: no boxes then.
      const own = shownFrame === frameId;
      detections = own ? found : [];
      boxesFrame = own ? frameId : null;
    } catch {
      if (!src) fallback();
    }
  }

  onDestroy(() => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
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
