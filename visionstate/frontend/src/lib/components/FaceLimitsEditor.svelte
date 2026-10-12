<script lang="ts">
  // Edits the face limits: the smallest face and the similarities. An empty field inherits (shown
  // in grey): on the Settings page the face model's default, on a sensor the global value.
  import { app } from '../app.svelte';
  import type { FaceLimits, FaceOverrides } from '../types';
  import { FACE_FIELDS } from '../ui';
  import LimitsEditor from './LimitsEditor.svelte';

  let {
    value = $bindable(),
    inherited,
    inheritedFrom,
  }: { value: FaceOverrides; inherited: FaceLimits; inheritedFrom: string } = $props();

  const limits = $derived(app.config?.face_limits);
  const below = [
    {
      pairs: [
        ['ask_similarity', 'match_similarity'],
        ['keep_similarity', 'match_similarity'],
      ],
      message: 'Asking and keeping the name must not be above “Recognised as a person”.',
    },
    { pairs: [['match_similarity', 'sure_similarity']], message: '“Recognised as a person” must not be above “Sure at once”.' },
  ] as const;
</script>

{#if limits}
  <LimitsEditor
    bind:value
    {inherited}
    {inheritedFrom}
    fields={FACE_FIELDS}
    {limits}
    {below}
    id="face"
    note="Similarities mean something else for each face model."
  />
{/if}
