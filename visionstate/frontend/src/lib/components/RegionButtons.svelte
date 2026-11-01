<script lang="ts">
  // What is done to a whole region beside its editor: turn a straightened one's top (``quad``), make
  // a polygon a rectangle again, or use the full frame (the wizard and Settings).
  import { isPolygon, toRectangle, turnTop } from '../roi';
  import type { Roi } from '../types';
  import Icon from './Icon.svelte';

  let { roi = $bindable(), quad = false }: { roi: Roi | null; quad?: boolean } = $props();
</script>

{#if quad && roi}
  <button type="button" class="btn sm" onclick={() => roi && (roi = turnTop(roi))} title="The edge clockwise from it becomes the top" data-testid="roi-turn">
    <Icon name="refresh" size={14} /> Turn
  </button>
{/if}
{#if isPolygon(roi)}
  <button type="button" class="btn sm" onclick={() => roi && (roi = toRectangle(roi))}>Reset to rectangle</button>
{/if}
<button type="button" class="btn sm" onclick={() => (roi = null)}><Icon name="frame" size={14} /> Use full frame</button>
