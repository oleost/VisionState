<script lang="ts">
  // What a sensor has in Home Assistant: whether it is there at all, whether values are sent, and
  // which entities exist. An entity chosen away is removed there.
  import { untrack } from 'svelte';
  import { api } from '../../lib/api';
  import { app, toast, toastError } from '../../lib/app.svelte';
  import ConfirmButton from '../../lib/components/ConfirmButton.svelte';
  import type { HaEntity, Sensor } from '../../lib/types';
  import { HA_MODES, type HaMode } from '../../lib/ui';

  let { sensor, onchange }: { sensor: Sensor; onchange: () => void } = $props();

  const modeOf = (s: Sensor): HaMode => (!s.in_ha ? 'none' : s.publish ? 'send' : 'hold');
  const initial = untrack(() => $state.snapshot(sensor));
  let mode = $state<HaMode>(modeOf(initial));
  let chosen = $state<Record<string, boolean>>(Object.fromEntries(initial.ha_entities.map((e) => [e.id, e.on])));
  let saving = $state(false);
  let snapshotAspect = $state(initial.objects?.snapshot_aspect ?? '');
  let snapshotMove = $state(Math.round((initial.objects?.snapshot_move ?? 0) * 100));
  const moveLimits = $derived(app.config!.object_limits.snapshot_move);

  const isOn = (e: HaEntity) => chosen[e.id] ?? e.on;
  // An object sensor's entities as a table: one row per object, one column per entity of it.
  const COLUMNS = ['detected', 'count', 'snapshot'] as const;
  const objects = $derived.by(() => {
    const rows = new Map<string, Partial<Record<(typeof COLUMNS)[number], HaEntity>>>();
    for (const e of sensor.ha_entities) {
      if (e.object && e.column) rows.set(e.object, { ...rows.get(e.object), [e.column]: e });
    }
    return [...rows];
  });
  const others = $derived(sensor.ha_entities.filter((e) => !e.object));
  const snapshotChosen = $derived(sensor.ha_entities.some((e) => e.column === 'snapshot' && isOn(e)));
  // What saving takes out of Home Assistant, from what it has now.
  const removing = $derived.by(() => {
    if (!sensor.in_ha) return [];
    return sensor.ha_entities.filter((e) => e.on && (mode === 'none' || !isOn(e)));
  });
  const removesAll = $derived(sensor.in_ha && mode === 'none');
  // Save only once something differs from what the sensor has (like the rest of its Settings).
  const changed = $derived(
    mode !== modeOf(sensor) ||
      sensor.ha_entities.some((e) => isOn(e) !== e.on) ||
      (!!sensor.objects &&
        (snapshotAspect !== (sensor.objects.snapshot_aspect ?? '') ||
          snapshotMove !== Math.round((sensor.objects.snapshot_move ?? 0) * 100))),
  );

  async function save() {
    saving = true;
    try {
      await api.updateSensor(sensor.id, {
        in_ha: mode !== 'none',
        publish: mode !== 'hold',
        ha_entities: Object.fromEntries(sensor.ha_entities.map((e) => [e.id, isOn(e)])),
        ...(sensor.objects ? { objects: { snapshot_aspect: snapshotAspect, snapshot_move: (snapshotMove ?? 0) / 100 } } : {}),
      });
      toast('Saved');
      onchange();
    } catch (err) {
      toastError(err);
    } finally {
      saving = false;
    }
  }
</script>

<div class="col page-col">
  <section class="card pad col" data-testid="ha-mode">
    <h3>In Home Assistant</h3>
    {#each Object.entries(HA_MODES) as [id, m] (id)}
      <label class="choice">
        <input type="radio" name="ha-mode" value={id} bind:group={mode} aria-label={m.label} />
        <span class="col" style="gap:2px">
          <strong class="small">{m.label}</strong>
          <span class="xsmall faint">{m.help}</span>
        </span>
      </label>
    {/each}
  </section>

  {#if mode !== 'none'}
    <section class="card pad col" data-testid="ha-entities">
      <h3>Entities</h3>
      <p class="small muted">
        What this sensor has in Home Assistant.
        {#if objects.length}A snapshot is the object cut from the frame: new when its count changes or it moves, and the last one stays after it has gone.{/if}
      </p>
      {#if objects.length}
        <table class="entities">
          <thead>
            <tr>
              <th></th>
              {#each COLUMNS as column (column)}<th class="tick">{column === 'detected' ? 'Detected' : column === 'count' ? 'Count' : 'Snapshot'}</th>{/each}
            </tr>
          </thead>
          <tbody>
            {#each objects as [name, row] (name)}
              <tr>
                <th scope="row" class="small">{name}</th>
                {#each COLUMNS as column (column)}
                  {@const entity = row[column]}
                  <td class="tick">
                    {#if entity}
                      <input type="checkbox" aria-label={entity.name} checked={isOn(entity)} onchange={(event) => (chosen[entity.id] = event.currentTarget.checked)} />
                    {/if}
                  </td>
                {/each}
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
      {#if snapshotChosen}
        <label class="col" style="gap:2px">
          <span class="small">Snapshot shape</span>
          <input class="input" type="text" maxlength="16" placeholder="free" bind:value={snapshotAspect} aria-label="Snapshot shape" />
          <span class="xsmall faint">free, or width:height such as 1:1, 4:3 or 16:9. It gets as close as the sensor’s region allows, and never cuts the object.</span>
        </label>
        <label class="col" style="gap:2px">
          <span class="small">New snapshot when the box has changed by</span>
          <span class="row"
            ><input
              class="input sm num"
              type="number"
              min={moveLimits[0] * 100}
              max={moveLimits[1] * 100}
              step="5"
              bind:value={snapshotMove}
              aria-label="New snapshot when the box has changed by"
            /> %</span
          >
          <span class="xsmall faint">Looked at each check, so something parked sends nothing new while something moving does. Every picture is a new row in Home Assistant’s history. 0: only when the count changes.</span>
        </label>
      {/if}
      {#each others as entity (entity.id)}
        <label class="choice">
          <input type="checkbox" aria-label={entity.name} checked={isOn(entity)} onchange={(event) => (chosen[entity.id] = event.currentTarget.checked)} />
          <span class="small">{entity.name}</span>
        </label>
      {/each}
    </section>
  {/if}

  {#if removing.length || removesAll}
    <p class="notice warn small" data-testid="ha-removing">
      {#if removesAll}Saving removes all of this sensor’s entities from Home Assistant{:else}Saving removes {removing.map((e) => e.name).join(', ')} from Home Assistant{/if},
      and automations and dashboards that use them stop working. Tick them again and Home Assistant gives them back the
      entity ID and name they had.
    </p>
    <div class="row">
      <ConfirmButton class="btn primary" onconfirm={save} confirmLabel="Tap again to remove">Save changes</ConfirmButton>
    </div>
  {:else}
    <div class="row"><button class="btn primary" disabled={saving || !changed} onclick={save}>{saving ? 'Saving…' : 'Save changes'}</button></div>
  {/if}
</div>

<style>
  .page-col {
    gap: var(--space-5);
    max-width: 640px;
  }
  .choice {
    display: flex;
    gap: var(--space-2);
    align-items: flex-start;
  }
  .choice input {
    margin-top: 3px;
  }
  .entities {
    width: 100%;
    border-collapse: collapse;
  }
  .entities th,
  .entities td {
    padding: var(--space-2) var(--space-1);
    border-bottom: 1px solid var(--c-border);
    text-align: left;
    font-weight: normal;
    overflow-wrap: anywhere;
  }
  .entities thead th {
    font-size: var(--text-xs, 12px);
    color: var(--c-muted);
  }
  .entities .tick {
    width: 72px;
    text-align: center;
  }
  /* A larger target for fingers. */
  .entities input {
    width: 20px;
    height: 20px;
  }
</style>
