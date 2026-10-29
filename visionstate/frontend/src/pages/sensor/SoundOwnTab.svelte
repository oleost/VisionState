<script lang="ts">
  // A sound sensor's own sounds: each with the clips it was taught from (play, forget), recordings
  // to add (a phone's voice memo, a video: their loudest seconds are kept), and "other sounds".
  import { api } from '../../lib/api';
  import { app, toast, toastError } from '../../lib/app.svelte';
  import ConfirmButton from '../../lib/components/ConfirmButton.svelte';
  import Icon from '../../lib/components/Icon.svelte';
  import ClipPlayer from '../../lib/components/sound/ClipPlayer.svelte';
  import { dateTime, plural } from '../../lib/format';
  import { href, paths } from '../../lib/router.svelte';
  import type { Sensor, SoundTaught } from '../../lib/types';
  import { SAMPLE_ORIGINS } from '../../lib/ui';

  let { sensor, onchange }: { sensor: Sensor; onchange: () => void } = $props();

  let taught = $state<SoundTaught | null>(null);
  let target = $state<string>(''); // an own sound's key, 'other', or '' for a new one
  let newName = $state('');
  let busy = $state(false);
  let input: HTMLInputElement;
  const max = $derived(app.config?.sound.max_custom ?? 10);

  async function load() {
    try {
      taught = await api.soundTaught(sensor.id);
      if (target && target !== 'other' && !taught.sounds.some((s) => s.key === target)) target = '';
    } catch (err) {
      toastError(err);
    }
  }
  load();

  async function add(e: Event) {
    const files = [...((e.currentTarget as HTMLInputElement).files ?? [])];
    if (!files.length) return;
    busy = true;
    try {
      const made = await api.uploadSoundClips(sensor.id, files, target || null, target ? undefined : newName.trim());
      toast(`${plural(made.added.length, 'clip')} taught`);
      if (!target) {
        target = made.added[0]?.label ?? '';
        newName = '';
      }
      await load();
      onchange();
    } catch (err) {
      toastError(err);
    } finally {
      busy = false;
      input.value = '';
    }
  }

  async function forgetClip(id: number) {
    try {
      await api.forgetSoundClip(id);
      await load();
      onchange();
    } catch (err) {
      toastError(err);
    }
  }

  async function forgetSound(key: string, name: string) {
    try {
      await api.forgetSound(sensor.id, key);
      toast(`${name} forgotten`);
      await load();
      onchange();
    } catch (err) {
      toastError(err);
    }
  }

  const clipsOf = (label: string) => (taught?.clips ?? []).filter((c) => c.label === label);
  const canAdd = $derived(!busy && (target !== '' || (newName.trim().length > 0 && (taught?.sounds.length ?? 0) < max)));
</script>

<div class="col page-gap">
  <p class="small muted">
    Teach the sensor sounds of your own — the doorbell, the washing machine's beep, a door — from a few clips each: from
    its <a href={href(paths.sensor(sensor.id, 'history'))}>History</a> (with clips on), or from recordings made with a phone.
    Each own sound is an on/off sensor in Home Assistant. About five clips is a good start; add one when it misses the sound,
    and add clips of what it is not as “other sounds”.
  </p>

  <section class="card pad col add">
    <h3>Add recordings</h3>
    <div class="row wrap" style="gap:var(--space-3)">
      <label class="field grow">
        As
        <select class="input" bind:value={target}>
          <option value="">A new own sound…</option>
          {#each taught?.sounds ?? [] as s (s.key)}<option value={s.key}>{s.name}</option>{/each}
          <option value="other">Other sounds (what they are not)</option>
        </select>
      </label>
      {#if target === ''}
        <label class="field grow">
          Name
          <input class="input" bind:value={newName} placeholder="Doorbell" maxlength="64" />
        </label>
      {/if}
    </div>
    <input bind:this={input} id="sound-files" class="sr-only" type="file" accept="audio/*,video/*" multiple onchange={add} disabled={!canAdd} />
    <label class="btn primary pick" class:disabled={!canAdd} for="sound-files" aria-disabled={!canAdd}>
      <Icon name="mic" size={16} /> {busy ? 'Adding…' : 'Choose or record'}
    </label>
    <p class="xsmall faint">Any sound or video file; its loudest four seconds are kept. On a phone, this can record right away.</p>
  </section>

  {#if taught}
    {#each taught.sounds as s (s.key)}
      <section class="card pad col" data-testid="own-sound-{s.key}">
        <div class="card-title">
          <h3>{s.name}</h3>
          <span class="chip {s.clips ? 'ok' : 'warn'}">{plural(s.clips, 'clip')}</span>
          <span class="spacer"></span>
          <ConfirmButton class="btn sm" onconfirm={() => forgetSound(s.key, s.name)} confirmLabel="Tap again to forget {s.name}">
            <Icon name="trash" size={14} /> Forget
          </ConfirmButton>
        </div>
        {#if !s.clips}<p class="small muted">No clips: it is never heard until it is taught some.</p>{/if}
        <div class="col clips">
          {#each clipsOf(s.key) as c (c.id)}
            <div class="row wrap clip">
              <ClipPlayer src={api.soundClipUrl(c.id)} label="Play the clip" />
              <span class="xsmall faint">{SAMPLE_ORIGINS[c.origin] ?? c.origin} · {dateTime(c.created_at)}</span>
              <span class="spacer"></span>
              <button type="button" class="btn sm ghost" onclick={() => forgetClip(c.id)} aria-label="Forget this clip"><Icon name="x" size={14} /></button>
            </div>
          {/each}
        </div>
      </section>
    {/each}
    <section class="card pad col">
      <div class="card-title"><h3>Other sounds</h3><span class="chip muted">{plural(taught.counts.other ?? 0, 'clip')}</span></div>
      <p class="small muted">
        What your own sounds are not. Each sensor also starts from 200 everyday sounds{sensor.sound?.learn_room
          ? `, and learns this room as it listens (${plural(taught.room, 'sound')} so far)`
          : ''}.
        {taught.trained ? '' : 'Teach an own sound to start.'}
      </p>
      <div class="col clips">
        {#each clipsOf('other') as c (c.id)}
          <div class="row wrap clip">
            <ClipPlayer src={api.soundClipUrl(c.id)} label="Play the clip" />
            <span class="xsmall faint">{SAMPLE_ORIGINS[c.origin] ?? c.origin} · {dateTime(c.created_at)}</span>
            <span class="spacer"></span>
            <button type="button" class="btn sm ghost" onclick={() => forgetClip(c.id)} aria-label="Forget this clip"><Icon name="x" size={14} /></button>
          </div>
        {/each}
      </div>
    </section>
  {/if}
</div>

<style>
  .page-gap {
    gap: var(--space-5);
    max-width: 900px;
  }
  .add {
    gap: var(--space-3);
  }
  .grow {
    flex: 1 1 220px;
    min-width: 0;
  }
  .pick {
    align-self: flex-start;
    min-height: var(--touch);
  }
  .pick.disabled {
    opacity: 0.5;
    pointer-events: none;
  }
  .clips {
    gap: var(--space-2);
  }
  .clip {
    gap: var(--space-3);
    align-items: center;
  }
  .clip :global(audio) {
    flex: 1 1 240px;
    min-width: 0;
  }
</style>
