<script lang="ts">
  // Settings → Backup: everything in one file (the parts ticked, all by default; camera passwords
  // only when ticked), and restoring one, which replaces everything here. Parts and defaults come
  // from the backend config, what each holds from ui.ts.
  import { api } from '../api';
  import { app, refreshStatus, toast, toastError } from '../app.svelte';
  import { size } from '../format';
  import { go, paths } from '../router.svelte';
  import type { BackupInfo, BackupPart } from '../types';
  import { BACKUP_PARTS } from '../ui';
  import ConfirmButton from './ConfirmButton.svelte';
  import Icon from './Icon.svelte';

  const config = $derived(app.config?.backup);
  let info = $state<BackupInfo | null>(null);
  let leftOut = $state<BackupPart[]>([]);
  let credentials = $state(app.config?.backup.credentials ?? false);
  let file = $state<File | null>(null);
  let input = $state<HTMLInputElement>();
  let progress = $state<number | null>(null); // upload 0–1; 1 = the server is restoring
  const result = $derived(app.restored);

  api.backup().then((b) => (info = b)).catch(toastError);

  const total = $derived(
    info ? (Object.entries(info.parts) as [BackupPart, number][]).reduce((sum, [k, v]) => sum + (leftOut.includes(k) ? 0 : v), 0) : 0,
  );

  function toggle(part: BackupPart, on: boolean) {
    leftOut = on ? leftOut.filter((p) => p !== part) : [...leftOut, part];
  }

  function choose(e: Event) {
    file = (e.currentTarget as HTMLInputElement).files?.[0] ?? null;
  }

  function cancel() {
    file = null;
    if (input) input.value = '';
  }

  async function restore() {
    if (!file) return;
    progress = 0;
    try {
      const restored = await api.restoreBackup(file, (done) => (progress = done));
      toast(`Backup restored: ${restored.sensors} sensor${restored.sensors === 1 ? '' : 's'}`);
      await refreshStatus();
      app.restored = restored; // Settings is made again, with everything that came back
    } catch (err) {
      toastError(err);
    } finally {
      progress = null;
      cancel();
    }
  }
</script>

<section class="card pad col" data-testid="backup">
  <h3>Backup</h3>
  <p class="small muted">
    Everything in one file: to move VisionState to another Home Assistant, or between the stable and the beta app.
    Home Assistant's own backups hold the app too, but not its history.
  </p>
  {#if config}
    <div class="col parts">
      {#each config.parts as part (part.key)}
        <label class="check small">
          <input
            type="checkbox"
            checked={!leftOut.includes(part.key)}
            disabled={!part.optional}
            onchange={(e) => toggle(part.key, e.currentTarget.checked)}
          />
          <span class="col grow">
            <span>{BACKUP_PARTS[part.key].label}{#if info}&nbsp;<span class="faint">· {size(info.parts[part.key])}</span>{/if}</span>
            <span class="xsmall faint">{BACKUP_PARTS[part.key].help}</span>
          </span>
        </label>
      {/each}
      <label class="check small">
        <input type="checkbox" bind:checked={credentials} />
        <span class="col grow">
          <span>Camera passwords</span>
          <span class="xsmall faint">
            {credentials
              ? 'Anyone who has the file can read them: keep it somewhere safe.'
              : 'Left out: enter them again after a restore.'}
          </span>
        </span>
      </label>
    </div>
    <a class="btn primary" style="align-self:flex-start" href={api.backupUrl(leftOut, credentials)} download>
      <Icon name="download" /> Download backup{#if info}&nbsp;· about {size(total)}{/if}
    </a>

    <h4>Restore</h4>
    <p class="small muted">
      Replaces everything here — sensors, people, training images and history — with a backup. Sensors that are in
      the backup keep their entities in Home Assistant; the others are removed.
    </p>
    <input bind:this={input} type="file" accept=".zip" class="sr-only" id="backup-restore" onchange={choose} />
    {#if !file}
      <label class="btn" for="backup-restore" style="align-self:flex-start"><Icon name="upload" /> Choose backup</label>
    {:else}
      <div class="notice warn small col confirm">
        <strong>Replace everything here with {file.name} ({size(file.size)})?</strong>
        <span>
          Download a backup of this app first if you may want it back. Stop the other VisionState app if it still runs
          (they share their entities in Home Assistant).
        </span>
        {#if info && file.size * 2 > info.free}
          <span>Free space here is {size(info.free)}: a restore needs about twice the backup's size.</span>
        {/if}
        <div class="row buttons">
          {#if progress === null}
            <ConfirmButton onconfirm={restore} confirmLabel="Click again to replace everything">Replace everything</ConfirmButton>
            <button class="btn" onclick={cancel}>Cancel</button>
          {:else}
            <span class="small" role="status">
              {progress < 1 ? `Uploading… ${Math.round(progress * 100)} %` : 'Restoring… this takes a moment'}
            </span>
          {/if}
        </div>
      </div>
    {/if}
    {#if result}
      <div class="notice small col" role="status">
        <strong>Restored {result.sensors} sensor{result.sensors === 1 ? '' : 's'} and {result.people} {result.people === 1 ? 'person' : 'people'}</strong>
        {#if result.app_version}<span class="faint">From VisionState {result.app_version}</span>{/if}
        {#if result.masked.length}
          <span>Enter the camera password again for:</span>
          <span class="row masked">
            {#each result.masked as sensor (sensor.id)}
              <button class="btn sm" onclick={() => go(paths.sensor(sensor.id, 'settings'))}>{sensor.name}</button>
            {/each}
          </span>
        {/if}
      </div>
    {/if}
  {:else}
    <p class="small muted">Loading…</p>
  {/if}
</section>

<style>
  .parts {
    gap: var(--space-2);
  }
  .parts .check {
    align-items: flex-start;
  }
  .grow {
    flex: 1;
    min-width: 0;
    gap: 2px;
  }
  h4 {
    margin: var(--space-3) 0 0;
  }
  .confirm,
  .masked,
  .buttons {
    gap: var(--space-2);
  }
  .masked,
  .buttons {
    flex-wrap: wrap;
  }
</style>
