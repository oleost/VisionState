<script lang="ts">
  // A reading sensor's region read upright (reading.straighten): four corners, its top edge marked, any
  // rotation and perspective undone (imaging.region_image). Shown with the region it changes.
  let { checked = $bindable(false), counter = false }: { checked: boolean; counter?: boolean } = $props();
</script>

<label class="check">
  <input type="checkbox" bind:checked aria-label="Correct rotation and perspective" data-testid="straighten" />
  <span class="col" style="gap:2px">
    <strong class="small">Correct rotation and perspective</strong>
    <span class="xsmall faint">
      For a {counter ? 'meter' : 'display'} the camera sees turned or at an angle: the region gets four corners and is
      read upright, as if the camera looked straight at it.
    </span>
  </span>
</label>

<style>
  .check {
    align-items: flex-start;
  }
  .check input {
    flex: none;
    margin-top: 3px;
  }
</style>
