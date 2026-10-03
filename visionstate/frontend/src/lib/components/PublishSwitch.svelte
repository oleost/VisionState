<script lang="ts">
  // Send the sensor's values to Home Assistant, or keep it to VisionState while it is being tuned:
  // then its entities stay unavailable (no values, so no statistics either). All kinds of sensor.
  let { publish = $bindable(true) }: { publish: boolean } = $props();
</script>

<label class="check">
  <input type="checkbox" bind:checked={publish} aria-label="Send to Home Assistant" />
  <span class="col" style="gap:2px">
    <strong class="small">Send to Home Assistant</strong>
    <span class="xsmall faint">
      {#if publish}
        Values go to Home Assistant as they are read.
      {:else}
        Off: the sensor runs and keeps its history and quality here, but its entities in Home Assistant stay
        <em>unavailable</em> — no values, no statistics. Turn it on once it reads reliably.
      {/if}
    </span>
  </span>
</label>

<style>
  .check {
    display: flex;
    gap: var(--space-2);
    align-items: flex-start;
  }
  .check input {
    margin-top: 3px;
  }
</style>
