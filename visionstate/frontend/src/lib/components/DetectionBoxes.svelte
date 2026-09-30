<script lang="ts">
  // Boxes around detected objects, drawn over an image (place inside RoiEditor, which is the
  // positioned frame). Boxes are normalised to the whole frame.
  import { pct } from '../format';
  import { objectColor, objectName } from '../objects';
  import type { Detection } from '../types';

  let {
    detections,
    classes,
    labels = true,
  }: { detections: Detection[]; classes: string[]; labels?: boolean } = $props();
</script>

{#each detections as d, i (i)}
  {@const color = objectColor(classes, d.key)}
  <div
    class="box"
    style:left="{d.box[0] * 100}%"
    style:top="{d.box[1] * 100}%"
    style:width="{(d.box[2] - d.box[0]) * 100}%"
    style:height="{(d.box[3] - d.box[1]) * 100}%"
    style:--c={color}
  >
    {#if labels}<span class="tag">{objectName(d.key)} {pct(d.score)}</span>{/if}
  </div>
{/each}

<style>
  .box {
    position: absolute;
    border: 2px solid var(--c);
    border-radius: 3px;
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.35);
    pointer-events: none;
  }
  .tag {
    position: absolute;
    left: -2px;
    bottom: 100%;
    padding: 1px 6px;
    border-radius: 3px 3px 0 0;
    background: var(--c);
    color: #12151a;
    font-size: var(--fs-xs);
    font-weight: 600;
    white-space: nowrap;
  }
</style>
