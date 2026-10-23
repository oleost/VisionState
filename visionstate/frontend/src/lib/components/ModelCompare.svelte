<script lang="ts">
  // The AI models side by side, per kind and from the smallest to the largest: what they are for,
  // rough memory and time, how accurate they are compared with each other, and whether they are
  // on disk — a downloaded model not in use can be deleted here (it is downloaded again when
  // chosen). Numbers come from the registries (GET /settings).
  import { api } from '../api';
  import { toast, toastError } from '../app.svelte';
  import { bySize, mb } from '../format';
  import type { BackboneInfo, ModelKind, SettingsInfo } from '../types';
  import { MODEL_COMPARE_NOTE, MODEL_KINDS } from '../ui';
  import ConfirmButton from './ConfirmButton.svelte';
  import Icon from './Icon.svelte';

  let { info, ondeleted }: { info: SettingsInfo; ondeleted: (info: SettingsInfo) => void } = $props();
  let open = $state(false);

  type Kind = (typeof MODEL_KINDS)[number]['key'];
  // Counters each name their wheel reader, so several can be in use.
  const chosen = (kind: Kind, id: string) =>
    kind === 'wheel_readers'
      ? info.wheel_readers_in_use.includes(id)
      : id === { backbones: info.backbone, detectors: info.detector, face_models: info.face_model, readers: info.reader }[kind];
  const deletable = (kind: Kind): kind is ModelKind => kind !== 'wheel_readers';

  async function remove(kind: ModelKind, m: BackboneInfo) {
    try {
      ondeleted(await api.deleteModel(kind, m.id));
      toast(`${m.name} deleted`);
    } catch (err) {
      toastError(err);
    }
  }
  const time = (ms: number) => (ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);
  const ram = (m: BackboneInfo) => (m.ram_mb >= 1000 ? `${(m.ram_mb / 1000).toFixed(1)} GB` : `${m.ram_mb} MB`);
</script>

<div class="col compare">
  <button class="btn sm ghost toggle" onclick={() => (open = !open)} aria-expanded={open}>
    <Icon name="chevron" size={14} />
    Compare and manage the models
  </button>
  {#if open}
    <div class="col kinds" data-testid="model-compare">
      {#each MODEL_KINDS as kind (kind.key)}
        <div class="col kind">
          <strong class="small">{kind.title}</strong>
          <span class="xsmall faint">{kind.text}</span>
          {#each bySize(info[kind.key] as BackboneInfo[]) as m (m.id)}
            <div class="model" class:current={chosen(kind.key, m.id)}>
              <span class="name small">
                {m.name}{#if chosen(kind.key, m.id)}<span class="xsmall muted"> · in use</span>{/if}
              </span>
              <span class="facts xsmall muted">
                <span>~{ram(m)} RAM</span>
                <span>~{time(m.time_ms)} per check</span>
                <span>{!m.installed ? `download ${mb(m.size)}` : m.downloaded ? `downloaded, ${mb(m.size)}` : 'included'}</span>
                <span class="accuracy" aria-label={`Accuracy ${m.accuracy} of 5`} title="Accuracy compared with the others">
                  {#each [1, 2, 3, 4, 5] as n (n)}<span class="pip" class:on={n <= m.accuracy}></span>{/each}
                </span>
              </span>
              {#if m.downloaded && deletable(kind.key)}
                {#if m.in_use}
                  <span class="xsmall faint">{m.in_use}</span>
                {:else}
                  {@const k = kind.key}
                  <ConfirmButton class="btn sm ghost delete" onconfirm={() => remove(k, m)} confirmLabel="Tap again to delete">
                    <Icon name="trash" size={14} /> Delete
                  </ConfirmButton>
                {/if}
              {/if}
            </div>
          {/each}
        </div>
      {/each}
      <p class="xsmall faint">{MODEL_COMPARE_NOTE}</p>
    </div>
  {/if}
</div>

<style>
  .compare {
    gap: var(--space-3);
  }
  .toggle {
    align-self: flex-start;
  }
  .toggle :global(svg) {
    transform: rotate(0deg);
    transition: transform 0.15s;
  }
  .toggle[aria-expanded='true'] :global(svg) {
    transform: rotate(90deg);
  }
  .kinds {
    gap: var(--space-4);
  }
  .kind {
    gap: var(--space-2);
  }
  .model {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: var(--space-2) var(--space-3);
    border-radius: var(--radius-md);
    background: var(--c-surface-2);
    border: 1px solid transparent;
  }
  .model.current {
    border-color: var(--c-accent-line);
  }
  .name {
    color: var(--c-text);
  }
  .model :global(.delete) {
    align-self: flex-start;
  }
  .facts {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-1) var(--space-3);
  }
  .accuracy {
    display: inline-flex;
    gap: 3px;
  }
  .pip {
    width: 8px;
    height: 8px;
    border-radius: var(--radius-pill);
    background: var(--c-border-strong);
  }
  .pip.on {
    background: var(--c-accent);
  }
</style>
