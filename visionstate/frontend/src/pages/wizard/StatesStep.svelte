<script lang="ts">
  // Wizard, "Detect" step of a state sensor: its states, and its entity in Home Assistant.
  import { app } from '../../lib/app.svelte';
  import StatesEditor from '../../lib/components/StatesEditor.svelte';
  import HaPreview from './HaPreview.svelte';

  let {
    entity,
    stateKeys,
    states = $bindable(),
  }: { entity: string; stateKeys: string[]; states: { name: string; color?: string }[] } = $props();

  const unknown = $derived(app.config?.unknown_state ?? 'unknown');
  const threshold = $derived(Math.round((app.config?.sensor_defaults.threshold ?? 0.7) * 100));
</script>

<div class="col" style="gap:var(--space-3)">
  <h3>Which states can it be in?</h3>
  <p class="small muted">Each state becomes an option on the Home Assistant sensor. <span class="kbd-only">Keys 1–9 label them later.</span></p>
</div>
<div style="max-width:520px"><StatesEditor bind:states /></div>
<HaPreview>
  <span class="mono">sensor.{entity}</span>
  <span class="mono xsmall muted">options: {[...stateKeys, unknown].join(', ')}</span>
  <span class="xsmall muted">Reports <span class="mono">{unknown}</span> when the AI is less than {threshold}% sure.</span>
</HaPreview>
