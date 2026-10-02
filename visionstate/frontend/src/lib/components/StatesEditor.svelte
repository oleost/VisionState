<script lang="ts">
  import { tick } from 'svelte';
  import { app } from '../app.svelte';
  import Icon from './Icon.svelte';

  type EditableState = { key?: string; name: string; color?: string };

  let { states = $bindable([]) }: { states: EditableState[] } = $props();

  const max = $derived(app.config?.max_states ?? 9);
  const palette = $derived(app.config?.state_palette ?? []);

  function colorFor(index: number, s: EditableState) {
    return s.color ?? palette[index % Math.max(palette.length, 1)] ?? 'var(--c-unknown)';
  }

  let inputs: HTMLInputElement[] = $state([]);

  async function add() {
    if (states.length >= max) return;
    states = [...states, { name: '' }];
    await tick();
    inputs[states.length - 1]?.focus(); // type the new name right away (also opens the phone keyboard)
  }

  function remove(index: number) {
    states = states.filter((_, i) => i !== index);
  }
</script>

<div class="col">
  {#each states as s, i (i)}
    <div class="row">
      <span class="kbd">{i + 1}</span>
      <label class="swatch" style:background={colorFor(i, s)} title="State colour">
        <input type="color" value={colorFor(i, s)} oninput={(e) => (s.color = e.currentTarget.value)} aria-label="Colour of state {i + 1}" />
      </label>
      <input class="input" bind:this={inputs[i]} bind:value={s.name} placeholder="State name, e.g. Open" aria-label="Name of state {i + 1}" />
      <button type="button" class="btn icon" aria-label="Remove state" disabled={states.length <= 2} onclick={() => remove(i)}>
        <Icon name="x" size={14} />
      </button>
    </div>
    {#if s.key}<span class="xsmall faint key">Home Assistant value: <span class="mono">{s.key}</span></span>{/if}
  {/each}
  <button type="button" class="btn ghost add" onclick={add} disabled={states.length >= max}>
    <Icon name="plus" size={14} /> Add state
  </button>
</div>

<style>
  .swatch {
    width: 22px;
    height: 22px;
    flex-shrink: 0;
    border-radius: var(--radius-pill);
    position: relative;
    cursor: pointer;
  }
  .swatch input {
    position: absolute;
    inset: 0;
    opacity: 0;
    cursor: pointer;
  }
  .add {
    align-self: flex-start;
    color: var(--c-accent);
    border-style: dashed;
  }
  .key {
    margin: -6px 0 0 70px;
  }
</style>
