<script lang="ts">
  // Boxes around detected objects, drawn over an image (place inside RoiEditor, which is the
  // positioned frame). Boxes are normalised to the whole frame. Labels never overlap: the most
  // certain object gets its label above the box, the next one inside the box if that is free,
  // and a label that fits nowhere is left out (the box stays).
  import { pct } from '../format';
  import { objectColor, objectName } from '../objects';
  import type { Detection } from '../types';

  let {
    detections,
    classes,
    labels = true,
  }: { detections: Detection[]; classes: string[]; labels?: boolean } = $props();

  let width = $state(0);
  let height = $state(0);

  const TAG_H = 17; // px, matches .tag (font-size xs + padding)
  const CHAR_W = 6.6; // px per character at font-size xs, a safe estimate

  type Rect = [number, number, number, number]; // x1, y1, x2, y2 in px
  const overlaps = (a: Rect, b: Rect) => a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];

  /** Per detection: 'above', 'inside' or null (no label). */
  const placement = $derived.by(() => {
    const result: ('above' | 'inside' | null)[] = detections.map(() => null);
    if (!labels || !width || !height) return result;
    const placed: Rect[] = [];
    const order = detections.map((_, i) => i).sort((a, b) => detections[b].score - detections[a].score);
    for (const i of order) {
      const d = detections[i];
      const text = `${objectName(d.key)} ${pct(d.score)}`;
      const w = text.length * CHAR_W + 14;
      const x = d.box[0] * width;
      const top = d.box[1] * height;
      const above: Rect = [x, top - TAG_H, x + w, top];
      const inside: Rect = [x, top, x + w, top + TAG_H];
      if (top >= TAG_H && !placed.some((r) => overlaps(r, above))) {
        result[i] = 'above';
        placed.push(above);
      } else if (!placed.some((r) => overlaps(r, inside))) {
        result[i] = 'inside';
        placed.push(inside);
      }
    }
    return result;
  });
</script>

<div class="layer" bind:clientWidth={width} bind:clientHeight={height}>
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
      {#if placement[i]}<span class="tag" class:inside={placement[i] === 'inside'}>{objectName(d.key)} {pct(d.score)}</span>{/if}
    </div>
  {/each}
</div>

<style>
  .layer {
    position: absolute;
    inset: 0;
    pointer-events: none;
  }
  .box {
    position: absolute;
    border: 2px solid var(--c);
    border-radius: 3px;
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.35);
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
  .tag.inside {
    bottom: auto;
    top: -2px;
    border-radius: 3px 0 3px 0;
  }
</style>
