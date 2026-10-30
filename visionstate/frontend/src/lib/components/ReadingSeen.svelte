<script lang="ts">
  // What the reader saw in a stored reading of a meter with pointer dials, so the digits the region
  // does not show can be checked. Shows nothing for a reading without dials.
  import { api } from '../api';
  import { readingDetail } from '../reading';
  import type { Prediction } from '../types';

  let { item }: { item: Prediction } = $props();
</script>

{#if readingDetail(item).dials?.length}
  <figure data-testid="reading-seen">
    <img src={api.historySeenUrl(item.id)} alt="The wheels and pointer dials as the reader saw them" />
    <figcaption class="xsmall faint">The wheels, then each pointer dial with the needle that was found drawn on it.</figcaption>
  </figure>
{/if}

<style>
  figure {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-1);
    margin: 0;
    text-align: center;
  }
  img {
    display: block;
    max-width: 100%;
    border-radius: var(--radius-sm);
    border: 1px solid var(--c-border);
  }
</style>
