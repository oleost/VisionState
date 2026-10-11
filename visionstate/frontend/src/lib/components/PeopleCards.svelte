<script lang="ts">
  // The people (shared by every sensor that recognises faces): one card per person with the faces
  // taught and the camera of each, where they were last seen, whether they need more faces,
  // look-alikes and faces that may carry the wrong name; renaming and forgetting. On the People
  // page, and on the Quality tab of a sensor that recognises faces (`sensor`: how faces look on
  // its camera, and links to it), reloaded with every poll of the sensor there.
  import { untrack } from 'svelte';
  import { api } from '../api';
  import { app, refreshStatus, toast, toastError } from '../app.svelte';
  import { ago, dateTime } from '../format';
  import { href, paths } from '../router.svelte';
  import type { People, Person, Sensor } from '../types';
  import ConfirmButton from './ConfirmButton.svelte';
  import FaceStats from './FaceStats.svelte';
  import Icon from './Icon.svelte';

  let { sensor = null, onchange = () => {} }: { sensor?: Sensor | null; onchange?: () => void } = $props();

  // Faces taught longer ago than this get a hint to add recent ones (children change quickly).
  const OLD_DAYS = 180;

  let people = $state<People | null>(null);
  async function load() {
    try {
      people = await api.people();
    } catch (err) {
      toastError(err);
    }
  }
  $effect(() => {
    void sensor;
    untrack(load);
  });

  const suggest = $derived(app.config?.faces.suggest_faces ?? 0);
  const nameOf = (key: string) => people?.people.find((p) => p.key === key)?.name ?? key;
  const lookAlikes = (p: Person) =>
    (people?.look_alikes ?? []).filter((l) => l.a === p.key || l.b === p.key).map((l) => nameOf(l.a === p.key ? l.b : l.a));
  const old = (p: Person) => !!p.oldest && Date.now() - Date.parse(p.oldest) > OLD_DAYS * 86_400_000;
  const enabled = $derived(!sensor || !!people?.sensors.includes(sensor.id));

  let renaming = $state<string | null>(null);
  let newName = $state('');
  function startRename(p: Person) {
    renaming = p.key;
    newName = p.name;
  }
  async function rename(e: SubmitEvent, p: Person) {
    e.preventDefault();
    const name = newName.trim();
    renaming = null;
    if (name && name !== p.name) await act(() => api.renamePerson(p.key, name), `${p.name} is now ${name}`);
  }
  function seen(p: Person): string {
    const where = p.seen.sensor_name ? ` on ${p.seen.sensor_name}` : '';
    if (p.seen.now !== 'not_seen') return `Seen now${where}`;
    return p.seen.at ? `Last seen ${ago(p.seen.at)}${where}` : 'Not seen yet';
  }

  async function act(action: () => Promise<unknown>, message: string) {
    try {
      await action();
      toast(message);
      onchange();
      load();
      refreshStatus();
    } catch (err) {
      toastError(err);
    }
  }
</script>

{#if people && (people.people.length || enabled)}
  <section class="card pad col" style="gap:var(--space-2)" data-testid="people">
    {#if sensor}<div class="card-title"><h3>People</h3></div>{/if}
    {#if sensor && !enabled}
      <div class="notice warn">
        Face recognition is off for this sensor under <a href={href(paths.sensor(sensor.id, 'settings'))}>Settings</a>.
      </div>
    {:else if sensor?.objects}
      <FaceStats objects={sensor.objects} />
    {/if}
    <p class="small muted">
      {#if people.people.length}
        Shared by every camera that recognises faces. Tap a person on a sensor's Live tab or in the history to teach
        more faces — in other light, from other angles and on each camera.
      {:else if sensor}
        Nobody taught yet: tap a person on the <a href={href(paths.sensor(sensor.id, 'live'))}>Live</a> tab and say who it is.
      {:else}
        Nobody taught yet: tap a person on the Live tab of a sensor that recognises faces and say who it is.
      {/if}
    </p>
  </section>

  {#each people.people as p (p.key)}
    <section class="card pad person">
      <div class="card-title">
        <div class="col" style="gap:2px">
          {#if renaming === p.key}
            <form class="row rename" onsubmit={(e) => rename(e, p)}>
              <input class="input sm" bind:value={newName} maxlength="64" aria-label="Name" />
              <button class="btn sm primary" type="submit">Save</button>
              <button class="btn sm" type="button" onclick={() => (renaming = null)}>Cancel</button>
            </form>
          {:else}
            <h3>{p.name} <span class="faint count">{p.faces.length}</span></h3>
          {/if}
          <span class="xsmall faint">
            {seen(p)}
            {#if p.faces.length < suggest} · teach a few more faces{/if}
          </span>
        </div>
        <div class="row wrap actions">
          {#if renaming !== p.key}
            <button class="btn sm" onclick={() => startRename(p)}>Rename</button>
          {/if}
          <ConfirmButton
            class="btn sm danger"
            onconfirm={() => act(() => api.forgetPerson(p.key), `${p.name} removed, with their faces and entities`)}
            confirmLabel="Tap again to remove"
          >
            <Icon name="trash" size={14} /> Remove person
          </ConfirmButton>
        </div>
      </div>
      {#each lookAlikes(p) as other (other)}
        <div class="notice warn xsmall">
          {p.name} and {other} look alike to the AI — teach both in more light and from more angles.
        </div>
      {/each}
      {#if old(p)}
        <div class="notice xsmall">Some faces were taught a long time ago. Add recent ones, above all for children.</div>
      {/if}
      <div class="faces">
        {#each p.faces as f (f.id)}
          <figure class:suspect={!!f.suspect}>
            <img src={api.faceImageUrl(f.id)} alt="A face of {p.name}" loading="lazy" />
            {#if f.is_night}<span class="badge">night</span>{/if}
            <button
              class="forget"
              onclick={() => act(() => api.forgetFace(f.id), 'Face forgotten')}
              aria-label="Forget this face"
              title="Forget this face"
            >
              <Icon name="x" size={12} />
            </button>
            {#if f.suspect}
              <button
                class="btn sm move"
                onclick={() => act(() => api.moveFace(f.id, f.suspect!), `Moved to ${nameOf(f.suspect!)}`)}
              >
                Looks like {nameOf(f.suspect)}: move
              </button>
            {/if}
            <figcaption class="xsmall faint" title={dateTime(f.created_at)}>
              {ago(f.created_at)}{f.sensor_name ? ` · ${f.sensor_name}` : ''}
            </figcaption>
          </figure>
        {/each}
      </div>
    </section>
  {/each}
{/if}

<style>
  .count {
    font-weight: 400;
    margin-left: 4px;
  }
  .actions {
    gap: var(--space-2);
    justify-content: flex-end;
  }
  .rename {
    gap: var(--space-2);
  }
  .rename input {
    max-width: 180px;
  }
  .person {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .faces {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(84px, 1fr));
    gap: var(--space-3);
  }
  figure {
    position: relative;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  figure img {
    width: 100%;
    aspect-ratio: 1;
    object-fit: cover;
    border-radius: var(--radius-sm);
    background: var(--c-sunken);
  }
  figure.suspect img {
    outline: 2px solid var(--c-warn);
  }
  .badge {
    position: absolute;
    left: 4px;
    top: 4px;
    padding: 0 6px;
    border-radius: var(--radius-pill);
    background: rgba(0, 0, 0, 0.6);
    color: #fff;
    font-size: var(--fs-xs);
  }
  .forget {
    position: absolute;
    right: 4px;
    top: 4px;
    width: 28px;
    height: 28px;
    border: 0;
    border-radius: 50%;
    background: rgba(0, 0, 0, 0.6);
    color: #fff;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
  }
  .move {
    white-space: normal;
    height: auto;
    min-height: 28px;
    text-align: left;
  }
</style>
