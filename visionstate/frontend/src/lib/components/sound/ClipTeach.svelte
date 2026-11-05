<script lang="ts">
  // Teach a clip of the history: as one of the sensor's own sounds, a new one, or "another sound"
  // (what the own sounds are not); a clip heard as a built-in sound (`soundKey`) also as that sound,
  // or as not that sound. Sounds are recognised from the clips taught to them.
  import { api } from '../../api';
  import { toast, toastError } from '../../app.svelte';
  import { notLabel, soundName } from '../../sound';
  import type { Sensor } from '../../types';
  import Icon from '../Icon.svelte';

  let {
    sensor,
    historyId,
    soundKey = null,
    onchange = () => {},
  }: { sensor: Sensor; historyId: number; soundKey?: string | null; onchange?: () => void } = $props();

  let name = $state('');
  let naming = $state(false);
  let busy = $state(false);
  let taught = $state<string | null>(null);
  const own = $derived(sensor.sound?.custom ?? []);
  // The built-in sound it was heard as, while the sensor listens for it.
  const builtin = $derived(
    soundKey && !own.some((c) => c.key === soundKey) && sensor.sound?.sounds.includes(soundKey) ? soundKey : null,
  );
  const names = $derived<Record<string, string>>(
    builtin ? { [builtin]: soundName(builtin, sensor), [notLabel(builtin)]: `not ${soundName(builtin, sensor)}` } : {},
  );

  async function teach(label: string | null, newName?: string) {
    busy = true;
    try {
      const result = await api.teachSoundClip(sensor.id, historyId, label, newName);
      taught =
        result.label === 'other'
          ? 'another sound'
          : (newName ?? names[result.label] ?? own.find((c) => c.key === result.label)?.name ?? result.label);
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
    {#if naming}
      <form
        class="row wrap new"
        onsubmit={(e) => {
          e.preventDefault();
          if (name.trim()) teach(null, name.trim());
        }}
      >
        <!-- svelte-ignore a11y_autofocus -->
        <input class="input sm" bind:value={name} placeholder="Name" maxlength="64" aria-label="Name of the new sound" autofocus />
        <button class="btn sm primary" type="submit" disabled={busy || !name.trim()}>Add sound</button>
        <button class="btn sm" type="button" onclick={() => (naming = false)}>Back</button>
      </form>
    {:else}
      <div class="row wrap" style="gap:var(--space-2)">
        {#if builtin}
          <button type="button" class="btn sm" disabled={busy} onclick={() => teach(builtin)}>{names[builtin]}</button>
          <button type="button" class="btn sm" disabled={busy} onclick={() => teach(notLabel(builtin))}>Not {names[builtin]}</button>
        {/if}
        {#each own as c (c.key)}
          <button type="button" class="btn sm" disabled={busy} onclick={() => teach(c.key)}>{c.name}</button>
        {/each}
        <button type="button" class="btn sm accent-outline" disabled={busy} onclick={() => (naming = true)}>
          <Icon name="plus" size={14} /> New sound…
        </button>
        <button type="button" class="btn sm ghost" disabled={busy} onclick={() => teach('other')}>Another sound</button>
      </div>
    {/if}
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
