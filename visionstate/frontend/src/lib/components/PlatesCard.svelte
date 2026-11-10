<script lang="ts">
  // Reading licence plates, under an object sensor's Settings → Objects: on or off, reporting plates
  // it does not know, and the household's cars (shared by every sensor that reads plates). Off by
  // default: turning it on (and reporting unknown plates) opens a sheet that says what it means
  // first. Its changes are saved at once (not with "Save changes"), like faces.
  import { api } from '../api';
  import { app, toast, toastError } from '../app.svelte';
  import { size } from '../format';
  import { objectName } from '../objects';
  import { href, paths } from '../router.svelte';
  import type { Car, PlateModelInfo, PlateSettings, Sensor } from '../types';
  import ConfirmButton from './ConfirmButton.svelte';
  import ConsentSheet from './ConsentSheet.svelte';
  import Icon from './Icon.svelte';

  let { sensor, onchange }: { sensor: Sensor; onchange: () => void } = $props();

  const vehicles = $derived(app.config?.plates.vehicles ?? []);
  const settings = $derived<PlateSettings>({ ...app.config!.plate_defaults, ...(sensor.objects?.plates ?? {}) });
  const looksForVehicles = $derived(!!sensor.objects?.classes.some((c) => vehicles.includes(c)));
  const status = $derived(app.status?.plates ?? null);
  const lookSteps = $derived.by(() => {
    const [low, high] = app.config?.plates.limits.look_distance ?? [0, 0];
    return Array.from({ length: Math.floor(high - low) + 1 }, (_, i) => low + i);
  });
  const lookText = (n: number) => (n === 0 ? 'No, only the plate' : `Plate up to ${n} ${n === 1 ? 'character' : 'characters'} off`);

  let model = $state<PlateModelInfo | null>(null);
  let cars = $state<Car[]>([]);
  let asking = $state<'on' | 'unknown' | null>(null);
  let busy = $state(false);
  // The car being added (key "") or changed.
  let editing = $state<Car | null>(null);

  api
    .settings()
    .then((s) => (model = s.plate_models.find((m) => m.id === s.plate_model) ?? null))
    .catch(toastError);
  const loadCars = () => api.cars().then((c) => (cars = c)).catch(toastError);
  loadCars();

  async function save(plates: Partial<PlateSettings>, message?: string) {
    busy = true;
    try {
      await api.updateSensor(sensor.id, { objects: { plates: { ...settings, ...plates } } });
      if (message) toast(message);
      onchange();
    } catch (err) {
      toastError(err);
    } finally {
      busy = false;
    }
  }

  async function saveCar() {
    if (!editing) return;
    busy = true;
    const body = { name: editing.name, plate: editing.plate, parent: editing.parent, look: editing.look ?? '' };
    try {
      if (editing.key) await api.changeCar(editing.key, body);
      else await api.addCar(body);
      toast(`${editing.name} saved`);
      editing = null;
      await loadCars();
      onchange();
    } catch (err) {
      toastError(err);
    } finally {
      busy = false;
    }
  }

  async function forget(car: Car) {
    try {
      await api.forgetCar(car.key);
      toast(`${car.name} forgotten`);
      await loadCars();
      onchange();
    } catch (err) {
      toastError(err);
    }
  }

  const newCar = (): Car => ({ key: '', name: '', plate: '', parent: sensor.objects?.classes.find((c) => vehicles.includes(c)) ?? 'car' });

  // Each car followed by the cars that share its look (the same model and colour, told apart by
  // their plates).
  const listed = $derived(
    cars.filter((c) => !c.look || !cars.some((o) => o.key === c.look)).flatMap((c) => [c, ...cars.filter((o) => o.look === c.key)]),
  );
  const nameOf = (key: string) => cars.find((c) => c.key === key)?.name ?? key;
  // The cars whose look the car being edited can share: not itself, not one that shares another's,
  // and none while other cars share its own.
  const lookChoices = $derived(
    editing && !cars.some((c) => c.look && c.look === editing?.key)
      ? cars.filter((c) => c.key !== editing?.key && !c.look)
      : [],
  );
  // Both cars' looks were taught already: they become one look (a mistake if they do not look alike).
  const bothTaught = $derived(
    !!editing?.look &&
      !!cars.find((c) => c.key === editing?.key)?.taught?.length &&
      !!cars.find((c) => c.key === editing?.look)?.taught?.length,
  );
  const sharedPlate = $derived(editing?.look ? (cars.find((c) => c.key === editing?.look)?.plate ?? '') : '');
  // The Kind select: a kind of vehicle, or "look:<car>" for the same look as one of the cars.
  const kindValue = $derived(editing ? (editing.look ? `look:${editing.look}` : editing.parent) : '');
  function setKind(value: string) {
    if (!editing) return;
    if (value.startsWith('look:')) {
      const look = value.slice(5);
      editing.look = look;
      editing.parent = cars.find((c) => c.key === look)?.parent ?? editing.parent;
    } else {
      editing.look = '';
      editing.parent = value;
    }
  }
</script>

<section class="card pad col plates" data-testid="plates-card">
  <h3>Cars and licence plates</h3>
  {#if !looksForVehicles}
    <p class="small muted">Add Car (or Truck, Bus, Motorcycle) to the objects to read licence plates.</p>
  {:else if !settings.enabled}
    <p class="small muted">
      Read the plates of the cars it sees: your own cars each get an on/off sensor in Home Assistant, and each car that
      comes is an event with its plate.
    </p>
    <div class="row">
      <button class="btn" onclick={() => (asking = 'on')}><Icon name="plus" size={14} /> Turn on…</button>
    </div>
  {:else}
    <label class="check">
      <input type="checkbox" checked disabled={busy} onchange={() => save({ enabled: false }, 'Plate reading is off')} />
      <span class="col" style="gap:2px">
        <strong class="small">Read licence plates</strong>
        <span class="xsmall faint">
          Off: only the cars whose look was taught on this camera stay its entities; the cars stay in the list (they are
          shared).
        </span>
      </span>
    </label>
    <label class="check">
      <input
        type="checkbox"
        checked={settings.unknown}
        disabled={busy}
        onchange={(e) => {
          if (e.currentTarget.checked) {
            e.currentTarget.checked = false;
            asking = 'unknown';
          } else save({ unknown: false }, 'Unknown plates are no longer reported');
        }}
      />
      <span class="col" style="gap:2px">
        <strong class="small">Report plates of cars I do not know</strong>
        <span class="xsmall faint">An event with the plate in Home Assistant each time a car that is not yours comes.</span>
      </span>
    </label>

    <label class="check">
      <input
        type="checkbox"
        checked={settings.learn}
        disabled={busy}
        onchange={(e) =>
          save({ learn: e.currentTarget.checked }, e.currentTarget.checked ? 'Learns the look of your cars' : 'No longer learns by itself')}
        data-testid="plate-learn"
      />
      <span class="col" style="gap:2px">
        <strong class="small">Learn a car's look from its plate</strong>
        <span class="xsmall faint">
          When a plate is read surely as one of your cars that it does not know by its look yet, that box is taught as the
          car now and then (shown under What you taught, where it can be forgotten).
        </span>
      </span>
    </label>

    <label class="col field">
      <strong class="small">Let a car's look help</strong>
      <select
        class="input"
        value={settings.look_distance}
        disabled={busy}
        onchange={(e) => save({ look_distance: Number(e.currentTarget.value) }, 'Saved')}
        data-testid="look-distance"
      >
        {#each lookSteps as n (n)}<option value={n}>{lookText(n)}</option>{/each}
      </select>
      <span class="xsmall faint">
        Teach a car's look by tapping its box on the Live tab and choosing the car. When the look says it is that car, a
        plate read a little off still names it at once (a reader often misses a character).
      </span>
    </label>

    {#if status?.error}
      <div class="notice warn col" style="gap:6px">
        <span class="small">The plate model could not be loaded: {status.error}</span>
        <div class="row">
          <button class="btn sm" onclick={() => api.retryPlates().catch(toastError)}><Icon name="refresh" size={14} /> Try again</button>
        </div>
      </div>
    {:else if status?.progress}
      <div class="col" style="gap:6px">
        <span class="small muted">Downloading the plate model… {size(status.progress.done)} of {size(status.progress.total)}</span>
        <progress max={status.progress.total} value={status.progress.done}></progress>
      </div>
    {:else if status && !status.loaded}
      <span class="small muted">Loading the plate model…</span>
    {/if}

    {#if model}
      <span class="xsmall faint">
        {model.name} · change under <a href={href(paths.settings())}>Settings → AI model</a>.
      </span>
    {/if}
  {/if}
  {#if looksForVehicles}
    <div class="col cars">
      <strong class="small">Your cars</strong>
      {#each listed as car (car.key)}
        <div class="row car" class:shared={!!car.look} data-testid="car">
          <span class="col" style="gap:2px;min-width:0">
            <span class="small">{car.name}</span>
            <span class="xsmall faint">
              {#if car.plate}<span class="mono">{car.plate}</span>{:else}No plate{/if} · {car.look
                ? `looks like ${nameOf(car.look)}`
                : objectName(car.parent)}{#if car.taught?.length}{` · look taught on ${car.taught.map((t) => t.name).join(', ')}`}{/if}
            </span>
          </span>
          <span class="spacer"></span>
          <button class="btn sm ghost icon" onclick={() => (editing = { ...car })} aria-label={`Change ${car.name}`} title="Change">
            <Icon name="draw" size={14} />
          </button>
          <ConfirmButton class="btn sm ghost danger" confirmLabel="Forget?" onconfirm={() => forget(car)}>
            <Icon name="trash" size={14} />
          </ConfirmButton>
        </div>
      {:else}
        <span class="xsmall faint">None yet: add the cars of the household, or name one by tapping it on the Live tab.</span>
      {/each}
      {#if editing}
        <form
          class="col add"
          onsubmit={(e) => {
            e.preventDefault();
            saveCar();
          }}
        >
          <label class="col field">
            <span class="xsmall muted">Name</span>
            <input class="input" bind:value={editing.name} placeholder="Our car" maxlength="64" required />
          </label>
          <label class="col field">
            <span class="xsmall muted">Plates (several: separate them with commas; none: known by its look)</span>
            <input class="input mono" bind:value={editing.plate} placeholder="AB 12345" maxlength="64" />
          </label>
          <label class="col field">
            <span class="xsmall muted">Kind</span>
            <select class="input" value={kindValue} onchange={(e) => setKind(e.currentTarget.value)} data-testid="car-kind">
              {#each vehicles as v (v)}<option value={v}>{objectName(v)}</option>{/each}
              {#if lookChoices.length}
                <optgroup label="The same look as">
                  {#each lookChoices as c (c.key)}<option value={`look:${c.key}`}>{c.name}</option>{/each}
                </optgroup>
              {/if}
            </select>
            {#if editing.look}
              <span class="xsmall faint">
                For two cars of the same model and colour: {nameOf(editing.look)} becomes the look of both (on when either is
                seen, also side-on), and each is named by its plate. Only for cars that look the same: choose its kind
                again to undo it.{#if bothTaught}{' '}<strong>Both looks were taught: they count as one from now on.</strong>{/if}{#if sharedPlate}{' '}Its plate <span class="mono">{sharedPlate}</span> moves to a car of its own under it.{/if}
              </span>
            {/if}
          </label>
          <div class="row" style="gap:8px">
            <button class="btn sm primary" type="submit" disabled={busy || !editing.name.trim()}>
              {editing.key ? 'Save' : 'Add car'}
            </button>
            <button class="btn sm" type="button" onclick={() => (editing = null)}>Cancel</button>
          </div>
        </form>
      {:else}
        <div class="row">
          <button class="btn sm" onclick={() => (editing = newCar())}><Icon name="plus" size={14} /> Add a car</button>
        </div>
      {/if}
      <span class="xsmall faint">
        Shared by every camera. A car is known by its plates where plates are read, and by its look on each camera it was
        taught on (tap it on the Live tab). Spaces and dashes in a plate do not matter.
      </span>
    </div>
  {/if}
</section>

{#if asking === 'on'}
  <ConsentSheet
    title={`Read licence plates on ${sensor.name}?`}
    confirm={model?.installed ? 'Turn on' : 'Download and turn on'}
    onclose={() => (asking = null)}
    onconfirm={() => {
      asking = null;
      save({ enabled: true }, 'Plate reading is on');
    }}
  >
    <li>
      Plates are read only on the cars, trucks, buses and motorcycles this sensor finds, and compared with the plates of
      <strong>your</strong> cars. A plate that is not yours is forgotten at once, unless you also turn on reporting them.
    </li>
    {#if model}
      <li>
        Needs the plate model <em>{model.name}</em> ({model.license}{model.installed
          ? ', already downloaded'
          : `, ${size(model.size)} downloaded once`}). {model.data}
      </li>
    {/if}
    <li>It is not a key: a copied plate reads the same. Do not open a gate on a plate alone.</li>
  </ConsentSheet>
{:else if asking === 'unknown'}
  <ConsentSheet
    title="Report plates of cars you do not know?"
    confirm="Report them"
    onclose={() => (asking = null)}
    onconfirm={() => {
      asking = null;
      save({ unknown: true }, 'Unknown plates are reported');
    }}
  >
    <li>
      Each car that is not yours makes an event in Home Assistant with its plate. Home Assistant keeps those events in its
      history, and VisionState keeps the frame in its own history for as long as you keep the history.
    </li>
    <li>
      A plate belongs to a person. Where this camera sees a street, a pavement or a neighbour's drive, recording the plates
      of passers-by may not be allowed where you live.
    </li>
  </ConsentSheet>
{/if}

<style>
  .cars {
    gap: var(--space-2);
    margin-top: var(--space-2);
  }
  .car {
    gap: var(--space-2);
    padding: var(--space-2) 0;
    border-bottom: 1px solid var(--c-border);
  }
  .car.shared {
    padding-left: var(--space-5); /* under the car whose look it shares */
  }
  .car .small {
    overflow-wrap: anywhere;
  }
  .add {
    gap: var(--space-2);
    padding: var(--space-3);
    border: 1px solid var(--c-border);
    border-radius: var(--radius-md);
  }
  .field {
    gap: 4px;
  }
</style>
