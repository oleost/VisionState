<script lang="ts">
  // Edits review rules. Global mode: every field has a value. Sensor mode: empty fields inherit the global value.
  import { app } from '../app.svelte';
  import type { ReviewOverrides, ReviewRules } from '../types';
  import { REVIEW_FIELDS } from '../ui';

  let {
    value = $bindable(),
    inherited = null,
    threshold = null,
  }: {
    value: ReviewRules | ReviewOverrides;
    /** Global rules; set in sensor mode so empty fields show what they inherit. */
    inherited?: ReviewRules | null;
    /** Sensor reporting threshold: the review limit never goes below it. */
    threshold?: number | null;
  } = $props();

  const sensorMode = $derived(inherited !== null);
  const limits = $derived(app.config?.review_limits);
  const rules = $derived(value as Record<string, number | boolean | null>);

  const round = (n: number) => Math.round(n * 100) / 100;
  const shown = (v: number | boolean | null | undefined, scale: number) => (typeof v === 'number' ? round(v * scale) : '');

  function set(key: string, raw: string, scale: number) {
    const record = value as Record<string, number | boolean | null>;
    record[key] = raw.trim() === '' && sensorMode ? null : Number(raw) / scale;
  }

  const enabledChoice = $derived(rules.enabled === null ? 'inherit' : rules.enabled ? 'on' : 'off');
  function setEnabled(choice: string) {
    (value as Record<string, boolean | null>).enabled = choice === 'inherit' ? null : choice === 'on';
  }
  const effectiveEnabled = $derived(rules.enabled ?? inherited?.enabled ?? true);
</script>

<div class="col rules">
  {#if sensorMode}
    <label class="line">
      <span>Review queue for this sensor</span>
      <select class="input sm pick" value={enabledChoice} onchange={(e) => setEnabled(e.currentTarget.value)}>
        <option value="inherit">Like global ({inherited?.enabled ? 'on' : 'off'})</option>
        <option value="on">On</option>
        <option value="off">Off</option>
      </select>
    </label>
  {:else}
    <label class="check"><input type="checkbox" bind:checked={(value as ReviewRules).enabled} /> Collect frames for review</label>
  {/if}

  {#if limits}
    <div class="col fields" class:dim={!effectiveEnabled}>
      {#each REVIEW_FIELDS as f (f.key)}
        {@const lim = limits[f.key]}
        <label class="line">
          <span>{f.label}</span>
          <span class="row">
            <input
              class="input sm num"
              type="number"
              min={round(lim[0] * f.scale)}
              max={round(lim[1] * f.scale)}
              step={f.step}
              value={shown(rules[f.key], f.scale)}
              placeholder={sensorMode ? String(shown(inherited?.[f.key], f.scale)) : ''}
              oninput={(e) => set(f.key, e.currentTarget.value, f.scale)}
              aria-label={f.label}
            />
            <span class="unit small muted">{f.unit}</span>
          </span>
        </label>
      {/each}
    </div>
  {/if}

  <p class="xsmall faint">
    {#if sensorMode}Empty fields use the global value (shown in grey), set under Settings → Review queue.{/if}
    {#if threshold !== null}Frames below the sensor's {Math.round(threshold * 100)}% threshold (reported as unknown) are always
      eligible.{/if}
  </p>
</div>

<style>
  .rules {
    gap: var(--space-3);
  }
  .fields {
    gap: var(--space-3);
  }
  .line {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-4);
    color: var(--c-text-2);
  }
  .pick {
    width: auto;
  }
  .unit {
    width: 36px;
  }
  .num::placeholder {
    color: var(--c-faint);
  }
  .dim {
    opacity: 0.5;
  }
</style>
