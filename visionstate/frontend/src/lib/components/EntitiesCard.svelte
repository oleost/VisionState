<script lang="ts">
  // The sensor's entities in Home Assistant, on its Live tab (every kind of sensor).
  import type { Sensor } from '../types';

  let { sensor }: { sensor: Sensor } = $props();
</script>

<section class="card pad">
  <div class="card-title"><h3>In Home Assistant</h3></div>
  <ul class="entities">
    {#each sensor.entity_ids as id (id)}<li class="mono xsmall">{id}</li>{/each}
  </ul>
</section>

<style>
  .entities {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
    overflow-wrap: anywhere;
  }
</style>
