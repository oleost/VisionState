<script lang="ts">
  // The AI models side by side, per kind: what they are for, rough memory and time, and how
  // accurate they are compared with each other. Numbers come from the registries (GET /settings).
  import { mb } from '../format';
  import type { BackboneInfo, SettingsInfo } from '../types';
  import { MODEL_COMPARE_NOTE, MODEL_KINDS } from '../ui';
  import Icon from './Icon.svelte';

  let { info }: { info: SettingsInfo } = $props();
  let open = $state(false);

  const chosen = (kind: (typeof MODEL_KINDS)[number]['key']) =>
    ({ backbones: info.backbone, detectors: info.detector, readers: info.reader, wheel_readers: info.wheel_reader })[kind];
  const time = (ms: number) => (ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);
  const ram = (m: BackboneInfo) => (m.ram_mb >= 1000 ? `${(m.ram_mb / 1000).toFixed(1)} GB` : `${m.ram_mb} MB`);
</script>

<div class="col compare">
  <button class="btn sm ghost toggle" onclick={() => (open = !open)} aria-expanded={open}>
    <Icon name="chevron" size={14} />
    Compare the models
  </button>
  {#if open}
    <div class="col kinds" data-testid="model-compare">
      {#each MODEL_KINDS as kind (kind.key)}
        <div class="col kind">
          <strong class="small">{kind.title}</strong>
          <span class="xsmall faint">{kind.text}</span>
          {#each info[kind.key] as m (m.id)}
            <div class="model" class:current={m.id === chosen(kind.key)}>
              <span class="name small">
                {m.name}{#if m.id === chosen(kind.key)}<span class="xsmall muted"> · in use</span>{/if}
              </span>
              <span class="facts xsmall muted">
                <span>~{ram(m)} RAM</span>
                <span>~{time(m.time_ms)} per check</span>
                <span>{m.installed ? 'on disk' : `download ${mb(m.size)}`}</span>
                <span class="accuracy" aria-label={`Accuracy ${m.accuracy} of 5`} title="Accuracy compared with the others">
                  {#each [1, 2, 3, 4, 5] as n (n)}<span class="pip" class:on={n <= m.accuracy}></span>{/each}
                </span>
              </span>
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
