<script lang="ts">
  // Edits the teaching limits of object sensors. An empty field inherits (shown in grey): on the
  // Settings page the state model's default, on a sensor the global value.
  import { app } from '../app.svelte';
  import type { TeachLimits, TeachOverrides } from '../types';
  import { TEACH_FIELDS } from '../ui';
  import LimitsEditor from './LimitsEditor.svelte';

  let {
    value = $bindable(),
    inherited,
    inheritedFrom,
  }: {
    value: TeachOverrides;
    /** What an empty field uses. */
    inherited: TeachLimits;
    /** Where that comes from, e.g. "the global value". */
    inheritedFrom: string;
  } = $props();

  const limits = $derived(app.config?.teach.limits);
  const below = [
    {
      pairs: [
        ['ask_similarity', 'match_similarity'],
        ['keep_similarity', 'match_similarity'],
      ],
      message: 'The second and third values must not be above the first.',
    },
  ] as const;
</script>

{#if limits}
  <LimitsEditor
    bind:value
    {inherited}
    {inheritedFrom}
    fields={TEACH_FIELDS}
    {limits}
    {below}
    id="teach"
    note="100% is the very same picture."
  />
{/if}
