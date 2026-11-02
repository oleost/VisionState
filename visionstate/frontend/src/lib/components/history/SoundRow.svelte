<script lang="ts">
  // A history row of a sound sensor: a sound began to be heard (with its clip, when clips are on)
  // or stopped. It opens to the clip — a video with sound on a camera — and the clip can be taught.
  import { api } from '../../api';
  import { dateTime, pct } from '../../format';
  import { soundColor, soundName } from '../../sound';
  import type { Prediction, Sensor } from '../../types';
  import { REVIEW_REASONS } from '../../ui';
  import Icon from '../Icon.svelte';
  import ClipPlayer from '../sound/ClipPlayer.svelte';
  import ClipTeach from '../sound/ClipTeach.svelte';

  let {
    item: p,
    sensor,
    showSensor = false,
    onchange = () => {},
  }: { item: Prediction; sensor: Sensor; showSensor?: boolean; onchange?: () => void } = $props();

  let open = $state(false);
  const heard = $derived(p.published_key === 'on');
  const answer = $derived((p.probs as Record<string, unknown>)?.answer as string | undefined);
  const muted = $derived(Number((p.probs as Record<string, unknown>)?.muted_s ?? 0));
</script>

<div class="card item">
  <button class="row head" onclick={() => (open = !open)} aria-expanded={open} disabled={!p.has_clip}>
    <span class="icon" class:on={heard}>
      <Icon name={p.clip_video ? 'film' : 'sound'} size={18} />
    </span>
    <span class="col" style="gap:4px;flex-grow:1;min-width:0">
      {#if showSensor}<span class="xsmall muted sensor">{sensor.name}</span>{/if}
      <span class="row wrap" style="gap:8px">
        <strong class="row" style="gap:8px">
          <span class="dot" style:background={heard ? soundColor(sensor, p.state_key) : 'var(--c-unknown)'}></span>
          {soundName(p.state_key, sensor)}
          {heard ? 'heard' : 'no longer heard'}
        </strong>
        {#if p.confidence}<span class="mono small muted">{pct(p.confidence)}</span>{/if}
        {#if p.review_reason && !p.reviewed}
          <span class="chip {REVIEW_REASONS[p.review_reason]?.tone ?? 'info'}">{REVIEW_REASONS[p.review_reason]?.label ?? p.review_reason}</span>
        {/if}
      </span>
      <span class="xsmall faint">{dateTime(p.created_at)}{p.has_clip ? (p.clip_video ? ' · video clip' : ' · sound clip') : ''}</span>
      {#if answer}<span class="xsmall muted">Answered: {answer === 'yes' ? 'yes' : answer === 'no' ? 'no' : soundName(answer, sensor)}</span>{/if}
    </span>
    {#if p.has_clip}<Icon name={open ? 'chevron-up' : 'chevron-down'} size={16} />{/if}
  </button>
  {#if open && p.has_clip}
    <div class="col full">
      <ClipPlayer src={api.historyClipUrl(p.id)} video={p.clip_video} />
      {#if muted}<span class="xsmall faint">{muted} s silenced: someone was talking.</span>{/if}
      <ClipTeach {sensor} historyId={p.id} {onchange} />
    </div>
  {/if}
</div>

<style>
  .item {
    padding: var(--space-2);
  }
  .head {
    width: 100%;
    gap: var(--space-3);
    padding: var(--space-1) var(--space-2) var(--space-1) var(--space-1);
    background: transparent;
    border: none;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .head:disabled {
    cursor: default;
  }
  .icon {
    width: 40px;
    height: 40px;
    flex-shrink: 0;
    border-radius: var(--radius-md);
    background: var(--c-surface-3);
    color: var(--c-muted);
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .icon.on {
    color: var(--c-accent);
  }
  .sensor {
    overflow-wrap: anywhere;
  }
  .full {
    margin-top: var(--space-2);
    gap: var(--space-3);
    padding: 0 var(--space-2) var(--space-2);
  }
</style>
