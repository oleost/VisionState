<script lang="ts">
  // Wizard, "Detect" step of a state sensor: the region to watch, its states, and its entity in Home Assistant.
  import { app } from '../../lib/app.svelte';
  import Icon from '../../lib/components/Icon.svelte';
  import RegionButtons from '../../lib/components/RegionButtons.svelte';
  import RoiEditor from '../../lib/components/RoiEditor.svelte';
  import StatesEditor from '../../lib/components/StatesEditor.svelte';
  import type { Roi } from '../../lib/types';
  import HaPreview from './HaPreview.svelte';

  let {
    entity,
    stateKeys,
    states = $bindable(),
    roi = $bindable(),
    frame,
    onnewframe,
  }: {
    entity: string;
    stateKeys: string[];
    states: { name: string; color?: string }[];
    roi: Roi | null;
    /** The frame from the first step (a state sensor has no test that would take one). */
    frame: string | null;
    onnewframe: () => void;
  } = $props();

  const unknown = $derived(app.config?.unknown_state ?? 'unknown');
  const threshold = $derived(Math.round((app.config?.sensor_defaults.threshold ?? 0.7) * 100));
</script>

<div class="col" style="gap:var(--space-3)">
  <h3>Which states can it be in?</h3>
  <p class="small muted">Each state becomes an option on the Home Assistant sensor. <span class="kbd-only">Keys 1–9 label them later.</span></p>
</div>
<div style="max-width:520px"><StatesEditor bind:states /></div>
<div class="col" style="gap:var(--space-3)">
  <div class="row wrap" style="gap:8px">
    <h3>What should it watch?</h3>
    <span class="spacer"></span>
    <button type="button" class="btn sm" onclick={onnewframe}><Icon name="refresh" size={14} /> New frame</button>
    <RegionButtons bind:roi />
  </div>
  <p class="small muted">
    Drag a box around what changes, with a small margin. The AI only looks inside it, which makes it much more accurate.
    {#if !roi}Without a box it looks at the whole picture.{/if}
  </p>
  <div class="frame"><RoiEditor src={frame} bind:roi editable /></div>
</div>
<HaPreview>
  <span class="mono">sensor.{entity}</span>
  <span class="mono xsmall muted">options: {[...stateKeys, unknown].join(', ')}</span>
  <span class="xsmall muted">Reports <span class="mono">{unknown}</span> when the AI is less than {threshold}% sure.</span>
</HaPreview>

<style>
  .frame {
    max-width: 900px;
    border-radius: var(--radius-lg);
    overflow: hidden;
  }
</style>
