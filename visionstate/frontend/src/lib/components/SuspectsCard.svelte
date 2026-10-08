<script lang="ts">
  // Samples whose label the model disagrees with (from cross-validation): confirm, relabel or delete.
  // Without `cell`: those not checked yet ("possibly mislabelled"). With `cell` (a red cell of the
  // confusion matrix was tapped): every sample counted there, also those already said to be right.
  import { api } from '../api';
  import { stateInfo, toast, toastError } from '../app.svelte';
  import { pct } from '../format';
  import type { Sensor, Suspect } from '../types';
  import Icon from './Icon.svelte';

  let {
    sensor,
    suspects,
    cell = null,
    onclearcell = () => {},
    onchange,
  }: {
    sensor: Sensor;
    suspects: Suspect[];
    cell?: { label: string; predicted: string; count: number } | null;
    onclearcell?: () => void;
    onchange: () => void;
  } = $props();

  let busy = $state(new Set<number>());

  const shown = $derived(
    cell ? suspects.filter((s) => s.label === cell.label && s.predicted === cell.predicted) : suspects.filter((s) => !s.verified),
  );
  // A cell can count samples no longer listed: relabelled or deleted since the last training.
  const missing = $derived(cell ? Math.max(0, cell.count - shown.length) : 0);

  async function act(id: number, action: () => Promise<unknown>, message: string) {
    busy = new Set([...busy, id]);
    try {
      await action();
      toast(message);
      onchange();
    } catch (err) {
      toastError(err);
    } finally {
      busy = new Set([...busy].filter((b) => b !== id));
    }
  }
</script>

<section class="card pad" id="suspects">
  <div class="card-title">
    {#if cell}
      <h3>
        Labelled <span style:color={stateInfo(sensor, cell.label).color}>{stateInfo(sensor, cell.label).name}</span>, the AI guessed
        <span style:color={stateInfo(sensor, cell.predicted).color}>{stateInfo(sensor, cell.predicted).name}</span>
      </h3>
      <button class="btn sm ghost" onclick={onclearcell}><Icon name="x" size={13} /> Show all possibly mislabelled</button>
    {:else}
      <h3>Possibly mislabelled</h3>
      <span class="xsmall faint">images the AI disagrees with — check that the label is right</span>
    {/if}
  </div>
  {#if shown.length}
    {#if cell}
      <p class="small muted intro">
        Each was guessed by a model that never saw it. If the label is wrong, change it; if it is right, label a few more like it so
        the AI learns the difference.
      </p>
    {/if}
    <div class="list">
      {#each shown as s (s.sample_id)}
        {@const label = stateInfo(sensor, s.label)}
        {@const guess = stateInfo(sensor, s.predicted)}
        <div class="item" class:busy={busy.has(s.sample_id)}>
          <img src={api.sampleImageUrl(s.sample_id)} alt="" loading="lazy" />
          <div class="col" style="gap:6px;min-width:0">
            <span class="small">
              Labelled <strong style:color={label.color}>{label.name}</strong>, the AI thinks
              <strong style:color={guess.color}>{guess.name}</strong> <span class="mono muted">{pct(s.confidence)}</span>
            </span>
            <div class="row wrap actions">
              {#if s.verified}
                <span class="chip ok"><Icon name="check" size={12} /> You said {label.name} is right</span>
              {:else}
                <button
                  class="btn sm"
                  onclick={() => act(s.sample_id, () => api.verifySamples(sensor.id, [s.sample_id]), `Kept as ${label.name}`)}
                >
                  <Icon name="check" size={13} /> {label.name} is right
                </button>
              {/if}
              {#each sensor.states.filter((st) => st.key !== s.label) as st (st.key)}
                <button
                  class="btn sm state"
                  class:suggested={st.key === s.predicted}
                  style:border-left-color={st.color}
                  onclick={() =>
                    act(s.sample_id, () => api.labelSamples(sensor.id, [s.sample_id], st.key), `Changed to ${st.name}`)}
                >
                  Change to {st.name}
                </button>
              {/each}
              <button
                class="btn sm ghost"
                aria-label="Delete image"
                onclick={() => act(s.sample_id, () => api.deleteSamples(sensor.id, [s.sample_id]), 'Image deleted')}
              >
                <Icon name="trash" size={13} />
              </button>
            </div>
          </div>
        </div>
      {/each}
    </div>
    {#if missing}
      <p class="xsmall faint more">{missing} more counted in this cell were relabelled or deleted since the last training.</p>
    {/if}
  {:else if cell}
    <p class="small muted">These images were relabelled or deleted since the last training; the matrix updates when it retrains.</p>
  {:else}
    <p class="small muted">Nothing suspicious — every label agrees with what the AI learned from the other images.</p>
  {/if}
</section>

<style>
  .intro {
    margin-bottom: var(--space-3);
  }
  .list {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .item {
    display: flex;
    align-items: center;
    gap: var(--space-4);
    padding: var(--space-2);
    border-radius: var(--radius-md);
    background: var(--c-sunken);
  }
  .item.busy {
    opacity: 0.5;
    pointer-events: none;
  }
  img {
    width: 140px;
    aspect-ratio: 16 / 10;
    object-fit: cover;
    border-radius: var(--radius-sm);
    flex-shrink: 0;
  }
  .actions {
    gap: var(--space-2);
  }
  .state {
    border-left-width: 4px;
  }
  .state.suggested {
    border-color: var(--c-accent-line);
    color: var(--c-accent);
  }
  .more {
    margin-top: var(--space-2);
  }
</style>
