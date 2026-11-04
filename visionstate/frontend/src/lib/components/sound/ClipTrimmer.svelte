<script lang="ts">
  // A recording to trim before it is taught: its waveform with a frame around the part kept. Drag
  // either end of the frame (or the frame itself) with a finger or the mouse; arrow keys move an
  // end. A mark shows where Stop was pressed. Play plays only the part kept. The sound is fetched
  // once and played from memory, so it seeks to the frame's start in every browser.
  import { onDestroy, untrack } from 'svelte';
  import Icon from '../Icon.svelte';

  let {
    src,
    peaks,
    perS,
    seconds,
    mark = null,
    min = 0.5,
    start = $bindable(0),
    end = $bindable(0),
  }: {
    src: string;
    peaks: number[];
    perS: number;
    seconds: number;
    mark?: number | null;
    min?: number;
    start?: number;
    end?: number;
  } = $props();

  const STEP_S = 0.1; // an arrow key moves an end this much

  let strip = $state<HTMLDivElement | undefined>();
  let audio: HTMLAudioElement | null = null;
  let url: string | null = null;
  let loadError = $state('');
  let playing = $state(false);
  let at = $state<number | null>(null); // the playhead while playing (seconds)
  let drag: { what: 'start' | 'end' | 'both'; x0: number; start0: number; end0: number } | null = null;

  const pct = (t: number) => `${(Math.max(0, Math.min(seconds, t)) / seconds) * 100}%`;
  const count = $derived(Math.max(peaks.length, 1));

  untrack(() => load());

  async function load() {
    try {
      const resp = await fetch(src);
      if (!resp.ok) throw new Error(`The recording could not be loaded (${resp.status})`);
      url = URL.createObjectURL(await resp.blob());
      audio = new Audio(url);
      audio.preload = 'auto';
      audio.addEventListener('ended', halt);
    } catch (err) {
      loadError = err instanceof Error ? err.message : String(err);
    }
  }

  onDestroy(() => {
    halt();
    if (url) URL.revokeObjectURL(url);
  });

  function halt() {
    audio?.pause();
    playing = false;
    at = null;
  }

  async function toggle() {
    if (!audio) return;
    if (playing) return halt();
    audio.currentTime = start;
    playing = true;
    try {
      await audio.play();
    } catch {
      return halt();
    }
    const tick = () => {
      if (!playing || !audio) return;
      at = audio.currentTime;
      if (audio.currentTime >= end) return halt();
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  function timeAt(clientX: number): number {
    const box = strip?.getBoundingClientRect();
    if (!box || !box.width) return 0;
    return ((clientX - box.left) / box.width) * seconds;
  }

  function set(what: 'start' | 'end' | 'both', t: number, start0 = start, end0 = end) {
    if (what === 'start') start = round(Math.max(0, Math.min(t, end - min)));
    else if (what === 'end') end = round(Math.min(seconds, Math.max(t, start + min)));
    else {
      const length = end0 - start0;
      const from = Math.max(0, Math.min(seconds - length, start0 + t));
      start = round(from);
      end = round(from + length);
    }
  }

  const round = (t: number) => Math.round(t * 100) / 100;

  function down(e: PointerEvent, what: 'start' | 'end' | 'both') {
    e.preventDefault();
    e.stopPropagation();
    halt();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag = { what, x0: e.clientX, start0: start, end0: end };
  }

  function move(e: PointerEvent) {
    if (!drag) return;
    if (drag.what === 'both') set('both', timeAt(e.clientX) - timeAt(drag.x0), drag.start0, drag.end0);
    else set(drag.what, timeAt(e.clientX));
  }

  function up() {
    drag = null;
  }

  function key(e: KeyboardEvent, what: 'start' | 'end') {
    const step = e.key === 'ArrowLeft' || e.key === 'ArrowDown' ? -STEP_S : e.key === 'ArrowRight' || e.key === 'ArrowUp' ? STEP_S : 0;
    if (!step) return;
    e.preventDefault();
    halt();
    set(what, (what === 'start' ? start : end) + step);
  }
</script>

<div class="col trimmer">
  <div class="strip" bind:this={strip} data-testid="clip-trimmer">
    <svg viewBox="0 0 {count} 100" preserveAspectRatio="none" aria-hidden="true">
      {#each peaks as p, i (i)}
        {@const h = Math.max(3, p * 92)}
        <rect x={i + 0.15} width="0.7" y={50 - h / 2} height={h} class:kept={i / perS >= start && i / perS < end} />
      {/each}
    </svg>
    <div class="shade" style:left="0" style:width={pct(start)}></div>
    <div class="shade" style:left={pct(end)} style:right="0"></div>
    {#if mark !== null && mark < seconds}
      <div class="mark" style:left={pct(mark)}><span class="xsmall">Stop</span></div>
    {/if}
    {#if at !== null}<div class="playhead" style:left={pct(at)}></div>{/if}
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
      class="frame"
      style:left={pct(start)}
      style:width="calc({pct(end)} - {pct(start)})"
      onpointerdown={(e) => down(e, 'both')}
      onpointermove={move}
      onpointerup={up}
      onpointercancel={up}
    >
      <div
        class="handle start"
        role="slider"
        tabindex="0"
        aria-label="Start of the clip"
        aria-valuemin={0}
        aria-valuemax={seconds}
        aria-valuenow={start}
        aria-valuetext="{start.toFixed(1)} s"
        onpointerdown={(e) => down(e, 'start')}
        onpointermove={move}
        onpointerup={up}
        onpointercancel={up}
        onkeydown={(e) => key(e, 'start')}
      ></div>
      <div
        class="handle end"
        role="slider"
        tabindex="0"
        aria-label="End of the clip"
        aria-valuemin={0}
        aria-valuemax={seconds}
        aria-valuenow={end}
        aria-valuetext="{end.toFixed(1)} s"
        onpointerdown={(e) => down(e, 'end')}
        onpointermove={move}
        onpointerup={up}
        onpointercancel={up}
        onkeydown={(e) => key(e, 'end')}
      ></div>
    </div>
  </div>
  <div class="row wrap readout">
    <button type="button" class="btn" onclick={toggle} disabled={!!loadError} data-testid="trim-play">
      <Icon name={playing ? 'stop' : 'play'} size={14} />
      {playing ? 'Stop' : 'Play the part kept'}
    </button>
    <span class="small mono" data-testid="trim-range">{start.toFixed(1)}–{end.toFixed(1)} s · {(end - start).toFixed(1)} s kept</span>
  </div>
  {#if loadError}<p class="small danger-text">{loadError}</p>{/if}
</div>

<style>
  .trimmer {
    gap: var(--space-3);
  }
  .strip {
    position: relative;
    height: 72px;
    margin: 0 14px; /* room for the handles at either end */
    border-radius: var(--radius-sm);
    background: var(--c-sunken);
    touch-action: pan-y;
    user-select: none;
    -webkit-user-select: none;
  }
  svg {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
  }
  rect {
    fill: var(--c-border-strong);
  }
  rect.kept {
    fill: var(--c-accent);
  }
  .shade {
    position: absolute;
    top: 0;
    bottom: 0;
    background: color-mix(in srgb, var(--c-sunken) 55%, transparent);
    pointer-events: none;
  }
  .mark {
    position: absolute;
    top: -4px;
    bottom: -4px;
    border-left: 2px dashed var(--c-warn);
    pointer-events: none;
  }
  .mark span {
    position: absolute;
    bottom: 100%;
    left: -2px;
    transform: translateX(-50%);
    color: var(--c-warn);
    white-space: nowrap;
  }
  .playhead {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 2px;
    margin-left: -1px;
    background: var(--c-text);
    pointer-events: none;
  }
  .frame {
    position: absolute;
    top: -3px;
    bottom: -3px;
    border: 3px solid var(--c-accent);
    border-radius: var(--radius-sm);
    cursor: grab;
    touch-action: none;
  }
  .handle {
    position: absolute;
    top: -3px;
    bottom: -3px;
    width: 14px;
    background: var(--c-accent);
    cursor: ew-resize;
    touch-action: none;
  }
  .handle::before {
    /* a wider place to grab than it looks (a thumb) */
    content: '';
    position: absolute;
    inset: -6px -12px;
  }
  .handle::after {
    content: '';
    position: absolute;
    top: 50%;
    left: 50%;
    width: 2px;
    height: 22px;
    transform: translate(-50%, -50%);
    border-radius: 1px;
    background: var(--c-accent-ink);
  }
  .handle.start {
    left: -14px;
    border-radius: var(--radius-sm) 0 0 var(--radius-sm);
  }
  .handle.end {
    right: -14px;
    border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  }
  .handle:focus-visible {
    outline: 2px solid var(--c-text);
    outline-offset: 2px;
  }
  .readout {
    gap: var(--space-3);
    align-items: center;
  }
  .danger-text {
    color: var(--c-danger-text);
  }
</style>
