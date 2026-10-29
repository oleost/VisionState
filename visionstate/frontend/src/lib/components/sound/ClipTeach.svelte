<script lang="ts">
  // Teach a clip of the history: as one of the sensor's own sounds, a new one, or "another sound"
  // (what the own sounds are not). Own sounds are recognised from the clips taught to them.
  import { api } from '../../api';
  import { toast, toastError } from '../../app.svelte';
  import type { Sensor } from '../../types';

  let { sensor, historyId, onchange = () => {} }: { sensor: Sensor; historyId: number; onchange?: () => void } = $props();

  let name = $state('');
  let busy = $state(false);
  let taught = $state<string | null>(null);
  const own = $derived(sensor.sound?.custom ?? []);

  async function teach(label: string | null, newName?: string) {
    busy = true;
    try {
      const result = await api.teachSoundClip(sensor.id, historyId, label, newName);
      taught = result.label === 'other' ? 'another sound' : (newName ?? own.find((c) => c.key === result.label)?.name ?? result.label);
      toast(result.made ? `New own sound: ${newName}` : `Taught as ${taught}`);
      name = '';
      onchange();
    } catch (err) {
      toastError(err);
    } finally {
      busy = false;
    }
  }
</script>

<div class="col teach">
  {#if taught}
    <span class="small ok">Taught as {taught} ✓</span>
  {:else}
    <span class="xsmall muted">Teach this clip as</span>
    <div class="row wrap" style="gap:var(--space-2)">
      {#each own as c (c.key)}
        <button type="button" class="btn sm" disabled={busy} onclick={() => teach(c.key)}>{c.name}</button>
      {/each}
      <button type="button" class="btn sm" disabled={busy} onclick={() => teach('other')}>Another sound</button>
    </div>
    <form
      class="row wrap new"
      onsubmit={(e) => {
        e.preventDefault();
        if (name.trim()) teach(null, name.trim());
      }}
    >
      <input class="input sm" bind:value={name} placeholder="A new own sound, e.g. Doorbell" maxlength="64" aria-label="Name of a new own sound" />
      <button class="btn sm primary" disabled={busy || !name.trim()}>Teach</button>
    </form>
  {/if}
</div>

<style>
  .teach {
    gap: var(--space-2);
  }
  .new {
    gap: var(--space-2);
  }
  .new input {
    flex: 1 1 200px;
    min-width: 0;
  }
  .ok {
    color: var(--c-accent);
  }
</style>
