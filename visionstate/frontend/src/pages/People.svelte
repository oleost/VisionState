<script lang="ts">
  // The people VisionState knows by their face, shared by every object sensor that recognises
  // faces: their faces (from every camera), where they were seen, renaming and forgetting. Each
  // person also has their own Home Assistant entity, sensor.visionstate_<key>: the sensor that
  // sees them now, or not_seen.
  import { api } from '../lib/api';
  import { app, refreshStatus, toast, toastError } from '../lib/app.svelte';
  import ConfirmButton from '../lib/components/ConfirmButton.svelte';
  import Icon from '../lib/components/Icon.svelte';
  import PeopleCards from '../lib/components/PeopleCards.svelte';

  let version = $state(0); // a new one reloads the cards

  const faces = $derived(app.status?.faces);

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
    there, and their own sensor in Home Assistant (<span class="mono">sensor.visionstate_…</span>) says which camera
    sees them now.
  </p>
  {#if faces && !faces.sensors}
    <div class="notice">
      No sensor recognises faces yet: turn it on under an object sensor's Settings → Faces (one that looks for Person).
    </div>
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
  .cards {
    gap: var(--space-4);
    max-width: 900px;
  }
  .forget {
    gap: var(--space-2);
    margin-top: var(--space-6);
  }
</style>
