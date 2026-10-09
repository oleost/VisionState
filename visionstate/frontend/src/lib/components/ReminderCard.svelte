<script lang="ts">
  // The review reminder: a notification in Home Assistant (and optionally a push to a phone) when
  // frames have waited for review a long time. Defaults and limits come from the backend config.
  import { api } from '../api';
  import { app, toast, toastError } from '../app.svelte';
  import { ago } from '../format';
  import type { ReminderInfo, ReminderRules } from '../types';

  let info = $state<ReminderInfo | null>(null);
  let rules = $state<ReminderRules | null>(null);
  let services = $state<string[]>([]);
  let historyDays = $state<number | null>(null);
  let saving = $state(false);
  let testing = $state(false);

  const limits = $derived(app.config?.reminder_limits);
  const haAvailable = $derived(app.status?.home_assistant ?? false);
  const changed = $derived(
    info !== null && rules !== null && (Object.keys(rules) as (keyof ReminderRules)[]).some((k) => rules![k] !== info![k]),
  );
  // Frames waiting for review are removed after twice the history days (Settings → Storage).
  const keptDays = $derived(historyDays === null ? null : historyDays * 2);
  const neverDue = $derived(rules !== null && keptDays !== null && rules.enabled && rules.after_days >= keptDays);

  function show(r: ReminderInfo) {
    info = r;
    const { sent_at: _, ...editable } = r;
    rules = editable;
  }

  api.reviewReminder().then(show).catch(toastError);
  api
    .notifyServices()
    .then((s) => (services = s))
    .catch(() => (services = []));
  api
    .storage()
    .then((s) => (historyDays = s.history_days))
    .catch(() => {});

  async function save() {
    if (!rules) return;
    saving = true;
    try {
      show(await api.saveReviewReminder(rules));
      toast('Reminder saved');
    } catch (err) {
      toastError(err);
    } finally {
      saving = false;
    }
  }

  async function test() {
    if (!rules) return;
    testing = true;
    try {
      const { push_error } = await api.testReviewReminder(rules);
      if (push_error) toast(`The notification is in Home Assistant, but the push failed: ${push_error}`, { tone: 'warn' });
      else toast(rules.notify_service ? 'Test sent to Home Assistant and the phone' : 'Test sent to Home Assistant');
    } catch (err) {
      toastError(err);
    } finally {
      testing = false;
    }
  }
</script>

<section class="card pad col">
  <h3>Review reminder</h3>
  <p class="small muted">
    A notification in Home Assistant when frames have waited for review a long time — never when one arrives. It goes away
    by itself once they are reviewed.
  </p>
  {#if rules && limits}
    <label class="check"><input type="checkbox" bind:checked={rules.enabled} /> Remind me in Home Assistant</label>
    <div class="col fields" class:dim={!rules.enabled}>
      <label class="line">
        <span>When the oldest frame has waited</span>
        <span class="row"
          ><input
            class="input sm num"
            type="number"
            min={limits.after_days[0]}
            max={limits.after_days[1]}
            step="1"
            bind:value={rules.after_days}
          />
          <span class="unit small muted">days</span></span
        >
      </label>
      <label class="line">
        <span>… and at least</span>
        <span class="row"
          ><input
            class="input sm num"
            type="number"
            min={limits.min_items[0]}
            max={limits.min_items[1]}
            step="1"
            bind:value={rules.min_items}
          />
          <span class="unit small muted">frames</span></span
        >
      </label>
      <label class="check"><input type="checkbox" bind:checked={rules.repeat} /> Remind again while they still wait</label>
      {#if rules.repeat}
        <label class="line">
          <span>Every</span>
          <span class="row"
            ><input
              class="input sm num"
              type="number"
              min={limits.repeat_days[0]}
              max={limits.repeat_days[1]}
              step="1"
              bind:value={rules.repeat_days}
            />
            <span class="unit small muted">days</span></span
          >
        </label>
      {/if}
      <label class="field">
        Also push to a phone
        <select class="input" bind:value={rules.notify_service}>
          <option value="">No push</option>
          {#each services as service (service)}
            <option value={service}>{service}</option>
          {/each}
          {#if rules.notify_service && !services.includes(rules.notify_service)}
            <option value={rules.notify_service}>{rules.notify_service} (not found)</option>
          {/if}
        </select>
        <span class="hint">A notify service of Home Assistant, such as the Companion app on a phone (notify.mobile_app_…).</span>
      </label>
    </div>
    {#if neverDue}
      <div class="notice warn small">
        Frames waiting for review are removed after {keptDays} days (twice the history days under Storage), so this reminder
        would never come. Choose fewer days, or keep the history longer.
      </div>
    {/if}
    {#if !haAvailable}
      <p class="xsmall faint">Needs the Home Assistant API, which is there when VisionState runs as a Home Assistant app.</p>
    {:else if info?.sent_at}
      <p class="xsmall faint">A reminder is up in Home Assistant, sent {ago(info.sent_at)}.</p>
    {/if}
    <div class="row buttons">
      <button class="btn primary" disabled={!changed || saving} onclick={save}>{saving ? 'Saving…' : 'Save reminder'}</button>
      <button class="btn" disabled={!haAvailable || testing} onclick={test}>{testing ? 'Sending…' : 'Send a test'}</button>
    </div>
  {:else}
    <p class="small muted">Loading…</p>
  {/if}
</section>

<style>
  .fields {
    gap: var(--space-3);
  }
  .line {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-4);
    font-size: var(--fs-base);
    color: var(--c-text-2);
  }
  .unit {
    width: 48px;
  }
  .dim {
    opacity: 0.5;
  }
  .buttons {
    flex-wrap: wrap;
  }
</style>
