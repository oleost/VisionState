<script lang="ts">
  // A sound sensor's own sounds, laid out like the People page: "Add an own sound" (a name and its
  // first recordings), then one card per sound with the clips it was taught from (play, forget) and
  // "Add recordings", and "Other sounds" (what they are not). A recording is a phone's voice memo
  // or any sound or video file: its loudest seconds are kept.
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

  const ACCEPT = 'audio/*,video/*';
  let taught = $state<SoundTaught | null>(null);
  let newName = $state('');
  let adding = $state<string | null>(null); // the label recordings are being added to ('' = a new sound)
  const max = $derived(app.config?.sound.max_custom ?? 10);
  const full = $derived((taught?.sounds.length ?? 0) >= max);

  async function load() {
    try {
      taught = await api.soundTaught(sensor.id);
    } catch (err) {
      toastError(err);
    }
  }
  load();

  /** Recordings for an own sound or "other" (`label`), or the first ones of a new sound (`label` ''). */
  async function add(e: Event, label: string) {
    const input = e.currentTarget as HTMLInputElement;
    const files = [...(input.files ?? [])];
    if (!files.length) return;
    adding = label;
    try {
      const made = await api.uploadSoundClips(sensor.id, files, label || null, label ? undefined : newName.trim());
      toast(`${plural(made.added.length, 'clip')} taught`);
      if (!label) newName = '';
      await load();
      onchange();
    } catch (err) {
      toastError(err);
    } finally {
      adding = null;
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
      toast(`${name} forgotten, with its clips and entity`);
      await load();
      onchange();
    } catch (err) {
      toastError(err);
    }
  }

  const clipsOf = (label: string) => (taught?.clips ?? []).filter((c) => c.label === label);
</script>

{#snippet addButton(label: string, id: string, text: string, disabled: boolean, primary = false)}
  <input type="file" accept={ACCEPT} multiple class="sr-only" {id} onchange={(e) => add(e, label)} {disabled} />
  <label class="btn {primary ? 'primary' : 'sm'}" for={id} class:disabled>
    <Icon name="mic" size={14} /> {adding === label ? 'Adding…' : text}
  </label>
{/snippet}

{#snippet clipList(label: string)}
  <div class="col clips">
    {#each clipsOf(label) as c (c.id)}
      <div class="row wrap clip">
        <ClipPlayer src={api.soundClipUrl(c.id)} label="Play the clip" />
        <span class="xsmall faint">{SAMPLE_ORIGINS[c.origin] ?? c.origin} · {dateTime(c.created_at)}</span>
        <span class="spacer"></span>
        <button type="button" class="btn sm ghost" onclick={() => forgetClip(c.id)} aria-label="Forget this clip"><Icon name="x" size={14} /></button>
      </div>
    {/each}
  </div>
{/snippet}

<div class="col page-gap">
  <p class="small muted">
    Teach the sensor sounds of your own — the doorbell, the washing machine's beep, a door — from a few clips each: from
    its <a href={href(paths.sensor(sensor.id, 'history'))}>History</a> (with clips on), or from recordings made with a phone.
    Each own sound is an on/off sensor in Home Assistant. About five clips is a good start; add one when it misses the sound,
    and add clips of what it is not as “other sounds”.
  </p>

  <section class="card pad col add" data-testid="add-own-sound">
    <h3>Add an own sound</h3>
    <p class="small muted">A name, and a few recordings of it: any sound or video file (its loudest four seconds are kept). On a phone, this can record right away.</p>
    {#if full}
      <p class="small warn-text">This sensor has {max} own sounds, the most it can have. Forget one to add another.</p>
    {:else}
      <div class="row wrap add-row">
        <input class="input" bind:value={newName} placeholder="Name, e.g. Doorbell" maxlength="64" aria-label="Name of the new own sound" />
        {@render addButton('', 'sound-files', 'Add recordings', !newName.trim() || adding !== null, true)}
      </div>
    {/if}
  </section>

  {#if taught}
    {#each taught.sounds as s (s.key)}
      <section class="card pad col" data-testid="own-sound-{s.key}">
        <div class="card-title">
          <div class="col" style="gap:2px">
            <h3>{s.name} <span class="faint count">{s.clips}</span></h3>
            <span class="xsmall faint">{s.clips ? plural(s.clips, 'clip') : 'No clips: it is never heard until it is taught some'}</span>
          </div>
          <div class="row wrap actions">
            {@render addButton(s.key, `sound-files-${s.key}`, 'Add recordings', adding !== null)}
            <ConfirmButton class="btn sm danger" onconfirm={() => forgetSound(s.key, s.name)} confirmLabel="Tap again to forget {s.name}">
              <Icon name="trash" size={14} /> Forget
            </ConfirmButton>
          </div>
        </div>
        {@render clipList(s.key)}
      </section>
    {/each}
    <section class="card pad col">
      <div class="card-title">
        <div class="col" style="gap:2px">
          <h3>Other sounds <span class="faint count">{taught.counts.other ?? 0}</span></h3>
          <span class="xsmall faint">What your own sounds are not</span>
        </div>
        <div class="row wrap actions">{@render addButton('other', 'sound-files-other', 'Add recordings', adding !== null)}</div>
      </div>
      <p class="small muted">
        Each sensor also starts from 200 everyday sounds{sensor.sound?.learn_room
          ? `, and learns this room as it listens (${plural(taught.room, 'sound')} so far)`
          : ''}.
        {taught.trained ? '' : 'Teach an own sound to start.'}
      </p>
      {@render clipList('other')}
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
  .add-row {
    gap: var(--space-2);
  }
  .add-row input {
    flex: 1 1 220px;
    max-width: 360px;
    min-width: 0;
  }
  .count {
    font-weight: 400;
    margin-left: 4px;
  }
  .actions {
    gap: var(--space-2);
    justify-content: flex-end;
  }
  label.disabled {
    opacity: 0.5;
    pointer-events: none;
  }
  .warn-text {
    color: var(--c-warn-text);
  }
  .clips {
    gap: var(--space-2);
  }
  .clip {
    gap: var(--space-3);
    align-items: center;
  }
</style>
