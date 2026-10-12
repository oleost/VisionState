<script lang="ts">
  // Does the camera give usable faces? "A usable face in 9 of the last 40 people looked at", and
  // when that share is low, what usually helps for the most common reason. Used on the Live tab,
  // the Faces card and the Quality tab.
  import { FACE_PROBLEMS } from '../objects';
  import type { FaceProblem, SensorObjects } from '../types';

  let { objects }: { objects: SensorObjects } = $props();

  // Below this share, with enough people looked at, a hint names the likely fix.
  const LOW_SHARE = 0.3;
  const ENOUGH = 10;

  const stats = $derived(objects.face_stats);
  const main = $derived(
    (Object.entries(stats.problems) as [FaceProblem, number][]).sort((a, b) => b[1] - a[1])[0]?.[0] ?? null,
  );
  const low = $derived(stats.looked >= ENOUGH && stats.usable / stats.looked < LOW_SHARE);
  const frame = $derived(stats.frame ? `${stats.frame[0]}×${stats.frame[1]}` : '');
</script>

<span class="xsmall faint">
  {#if !stats.looked}
    No person looked at for a face yet.
  {:else}
    A usable face in {stats.usable} of the last {stats.looked} people looked at{main ? ` (mostly: ${FACE_PROBLEMS[main]})` : ''}.
  {/if}
</span>
{#if low}
  <span class="xsmall warn">
    {#if main === 'small'}
      Faces here are mostly too small: a face needs to be {stats.min_face_px ?? ''} pixels wide{frame
        ? `, and this camera sends ${frame} pictures`
        : ''}. Use the camera's main stream (its highest resolution) rather than a sub-stream or a small snapshot,
      or a camera closer to where people pass.
    {:else if main === 'cut'}
      Faces here are often cut off by the edge of the picture. A camera that sees people whole helps.
    {:else if main === 'turned' || main === 'none'}
      Faces here are rarely usable: people seldom face this camera. A camera that people walk towards helps.
    {:else}
      Faces here are rarely usable. A sharper picture (the camera's main stream over RTSP instead of the snapshot)
      or more light helps.
    {/if}
  </span>
{/if}

<style>
  .warn {
    color: var(--c-warn);
  }
</style>
