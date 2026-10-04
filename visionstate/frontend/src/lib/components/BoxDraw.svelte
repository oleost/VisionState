<script lang="ts">
  // Draw one box over an image by dragging (mouse or finger), e.g. around an object the detector
  // missed. Place inside RoiEditor (the positioned frame). The box is normalised to the image;
  // dragging again draws a new one. While it is shown, the frame does not scroll under a finger.
  import { clamp01, type Pt } from '../roi';

  let { box = $bindable(null) }: { box: [number, number, number, number] | null } = $props();

  const MIN_SIZE = 0.02; // smallest box, as a share of the image

  let layer: HTMLDivElement;
  let start: Pt | null = null;

  function point(e: PointerEvent): Pt {
    const r = layer.getBoundingClientRect();
    return [clamp01((e.clientX - r.left) / r.width), clamp01((e.clientY - r.top) / r.height)];
  }

  function down(e: PointerEvent) {
    e.preventDefault();
    try {
      layer.setPointerCapture(e.pointerId);
    } catch {
      /* pointer no longer active */
    }
    start = point(e);
    box = null;
  }

  function move(e: PointerEvent) {
    if (!start) return;
    const p = point(e);
    box = [Math.min(start[0], p[0]), Math.min(start[1], p[1]), Math.max(start[0], p[0]), Math.max(start[1], p[1])];
  }

  function up() {
    if (box && (box[2] - box[0] < MIN_SIZE || box[3] - box[1] < MIN_SIZE)) box = null;
    start = null;
  }
</script>

<div
  class="draw"
  bind:this={layer}
  onpointerdown={down}
  onpointermove={move}
  onpointerup={up}
  onpointercancel={up}
  role="presentation"
>
  {#if box}
    <div
      class="drawn"
      style:left="{box[0] * 100}%"
      style:top="{box[1] * 100}%"
      style:width="{(box[2] - box[0]) * 100}%"
      style:height="{(box[3] - box[1]) * 100}%"
    ></div>
  {/if}
</div>

<style>
  .draw {
    position: absolute;
    inset: 0;
    cursor: crosshair;
    touch-action: none; /* drawing, not scrolling */
    background: rgba(8, 10, 13, 0.25);
  }
  .drawn {
    position: absolute;
    border: 2px solid var(--c-accent);
    border-radius: 3px;
    background: color-mix(in srgb, var(--c-accent) 15%, transparent);
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.4);
  }
</style>
