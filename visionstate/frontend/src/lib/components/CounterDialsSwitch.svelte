<script lang="ts">
  // A counter read by a wheel reader can also have pointer dials: which of them the picture edits
  // (the wizard and Settings).
  let { editing = $bindable(), dials }: { editing: 'counter' | 'dials'; dials: number } = $props();
</script>

<div class="row wrap switch" role="group" aria-label="What the picture edits">
  <span class="small muted">Edit:</span>
  <button type="button" class="btn sm" class:primary={editing === 'counter'} aria-pressed={editing === 'counter'} onclick={() => (editing = 'counter')}>
    Counter
  </button>
  <button type="button" class="btn sm" class:primary={editing === 'dials'} aria-pressed={editing === 'dials'} onclick={() => (editing = 'dials')} data-testid="edit-dials">
    Dials{dials ? ` (${dials})` : ''}
  </button>
  {#if editing === 'counter' && !dials}
    <span class="xsmall faint">Has the meter pointer dials (×0.1, ×0.01 …)? Add them under <em>Dials</em>.</span>
  {/if}
</div>

<style>
  .switch {
    gap: 8px;
  }
</style>
