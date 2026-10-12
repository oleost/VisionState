<script lang="ts">
  // The people VisionState knows by their face, shared by every object sensor that recognises
  // faces: their faces (from every camera), where they were seen, renaming and forgetting. Each
  // person also has their own Home Assistant entity, sensor.visionstate_<key>_seen_by: the sensor that
  // sees them now, or not_seen.
  import { api } from '../lib/api';
  import { app, refreshStatus, toast, toastError } from '../lib/app.svelte';
  import ConfirmButton from '../lib/components/ConfirmButton.svelte';
  import Icon from '../lib/components/Icon.svelte';
  import PeopleCards from '../lib/components/PeopleCards.svelte';
  import { photoMessage } from '../lib/objects';
  import { PHOTO_ACCEPT } from '../lib/ui';

  let version = $state(0); // a new one reloads the cards

  const faces = $derived(app.status?.faces);
  const room = $derived((faces?.people ?? 0) < (app.config?.faces.max_people ?? 0));

  let newName = $state('');
  let adding = $state(false);
  async function addPerson(e: Event) {
    const input = e.currentTarget as HTMLInputElement;
    const files = [...(input.files ?? [])];
    input.value = '';
    const name = newName.trim();
    if (!files.length || !name) return;
    adding = true;
    try {
      const result = await api.addPhotos({ new_person: name }, files);
      toast(photoMessage(result));
      if (result.added) {
        newName = '';
        version += 1;
        refreshStatus();
      }
    } catch (err) {
      toastError(err);
    } finally {
      adding = false;
    }
  }

  async function forgetEveryone() {
    try {
      await api.forgetPeople();
      toast('Everyone is forgotten, with their faces and entities');
      version += 1;
      refreshStatus();
    } catch (err) {
      toastError(err);
    }
  }
</script>

<div class="page">
  <h1>People</h1>
  <p class="muted intro">
    The people you named by their face. Every camera that recognises faces knows them: each gets an on/off sensor
    there, and their own sensors in Home Assistant say which camera sees them now
    (<span class="mono">sensor.visionstate_…_seen_by</span>) and when they were last seen
    (<span class="mono">…_last_seen</span>).
  </p>
  {#if faces && !faces.sensors}
    <div class="notice">
      No sensor recognises faces yet: turn it on under an object sensor's Settings → Faces (one that looks for Person).
    </div>
  {/if}
  {#if room && faces?.sensors}
    <section class="card pad col add" data-testid="add-person">
      <h3>Add a person</h3>
      <p class="small muted">
        A quick start: a few photos of their face, taken with the phone or picked from its gallery, or a short video
        turning the head a little. Faces taught on each camera (tap a person on its Live tab) recognise them best.
      </p>
      <div class="row wrap add-row">
        <input class="input" bind:value={newName} placeholder="Name" maxlength="64" aria-label="Name of the new person" />
        <input
          type="file"
          accept={PHOTO_ACCEPT}
          multiple
          class="sr-only"
          id="new-person-photos"
          onchange={addPerson}
          disabled={!newName.trim() || adding}
        />
        <label class="btn primary" for="new-person-photos" class:disabled={!newName.trim() || adding}>
          <Icon name="camera" size={14} /> {adding ? 'Adding…' : 'Add photos'}
        </label>
      </div>
    </section>
  {/if}
  {#key version}
    <div class="col cards"><PeopleCards /></div>
  {/key}
  {#if faces?.people}
    <div class="row wrap forget">
      <ConfirmButton class="btn sm danger" onconfirm={forgetEveryone} confirmLabel="Tap again to forget everyone">
        <Icon name="trash" size={14} /> Forget everyone
      </ConfirmButton>
      <span class="xsmall faint">Every person, their faces and their entities, on every camera.</span>
    </div>
  {/if}
</div>

<style>
  .intro {
    max-width: 720px;
  }
  .add {
    gap: var(--space-2);
    max-width: 900px;
    margin-bottom: var(--space-4);
  }
  .add-row {
    gap: var(--space-2);
  }
  .add-row .input {
    max-width: 240px;
  }
  label.disabled {
    opacity: 0.5;
    pointer-events: none;
  }
  .cards {
    gap: var(--space-4);
    max-width: 900px;
  }
  .forget {
    gap: var(--space-2);
    margin-top: var(--space-6);
  }
</style>
