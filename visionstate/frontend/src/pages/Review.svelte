<script lang="ts">
  import { api } from '../lib/api';
  import { refreshStatus, stateInfo, toast, toastError } from '../lib/app.svelte';
  import ConfirmButton from '../lib/components/ConfirmButton.svelte';
  import DetectionBoxes from '../lib/components/DetectionBoxes.svelte';
  import Icon from '../lib/components/Icon.svelte';
  import ReadingVerdict from '../lib/components/ReadingVerdict.svelte';
  import RoiEditor from '../lib/components/RoiEditor.svelte';
  import { dateTime, pct } from '../lib/format';
  import { objectName } from '../lib/objects';
  import { REJECT_REASONS, readingDetail, readingRoi, readingUnit } from '../lib/reading';
  import type { ObjectQuestion, ReviewItem } from '../lib/types';
  import { REVIEW_REASONS, TONE_COLOR } from '../lib/ui';

  let items = $state<ReviewItem[] | null>(null);
  let total = $state(0);
  let waiting = $state<{ id: number; name: string; count: number }[]>([]);
  let index = $state(0); // the next item waiting for an answer
  let editing = $state<number | null>(null); // an answered item opened again from the list
  let answers = $state<Record<number, string>>({});
  let given = $state<Record<number, string | null>>({}); // state answered per item (null: skipped)
  let busy = $state(false);

  async function load() {
    try {
      const result = await api.review();
      items = result.items;
      total = result.total;
      waiting = result.sensors;
      index = 0;
      editing = null;
      refreshStatus(); // keep the nav badge in step with the list
      answers = {};
      given = {};
    } catch (err) {
      toastError(err);
    }
  }
  load();

  const shown = $derived(editing ?? index);
  const current = $derived(items?.[shown] ?? null);
  const isReading = (item: ReviewItem) => item.sensor.kind === 'reading';
  /** An object sensor's question: may this box be one of its own labels? */
  const question = (item: ReviewItem) => (item.sensor.kind === 'objects' ? ((item.probs as unknown as { ask?: ObjectQuestion }).ask ?? null) : null);
  const labelName = (item: ReviewItem, key: string) => objectName(key, item.sensor.labels);
  const unitOf = (item: ReviewItem) => (item.sensor.reading ? readingUnit(item.sensor.reading) : '');
  /** Sidebar line: what was read, or the state the AI predicted. */
  const summary = (item: ReviewItem) => {
    const q = question(item);
    if (q) return `${labelName(item, q.label)}?`;
    return isReading(item) ? `“${readingDetail(item).text || '—'}”` : `${stateInfo(item.sensor, item.state_key).name} ${pct(item.confidence)}`;
  };

  /** One item of this sensor left the queue (answered or skipped): keep the counts in step. */
  function countDown(sensorId: number) {
    waiting = waiting.map((w) => (w.id === sensorId ? { ...w, count: w.count - 1 } : w)).filter((w) => w.count > 0);
  }

  /** An answer was saved: on to the next waiting item, or back to it after changing an earlier answer. */
  function answered(text: string) {
    if (!current) return;
    answers[current.id] = text;
    if (editing === null) {
      countDown(current.sensor.id);
      index += 1;
    } else {
      editing = null;
    }
    refreshStatus();
  }

  function readingAnswered(verdict: { read_ok: boolean | null; correct_value: string | null }) {
    if (!current || !items) return;
    const text =
      verdict.read_ok === null && current.read_ok === null
        ? 'Skipped'
        : verdict.read_ok
          ? '✓ Read correctly'
          : `✓ Misread${verdict.correct_value ? ` — was ${verdict.correct_value}` : ''}`;
    items[shown] = { ...current, ...verdict }; // opened again, it shows this verdict
    answered(text);
  }
  async function dismissAll(sensor: { id: number; name: string }) {
    if (busy) return;
    busy = true;
    try {
      const { dismissed } = await api.dismissReview(sensor.id);
      toast(`Dismissed ${dismissed} item${dismissed === 1 ? '' : 's'} of ${sensor.name}`);
      await load();
    } catch (err) {
      toastError(err);
    } finally {
      busy = false;
    }
  }

  const predicted = $derived(current ? stateInfo(current.sensor, current.state_key) : null);
  const others = $derived(current ? current.sensor.states.filter((s) => s.key !== current.state_key) : []);

  async function answer(action: 'confirm' | 'label' | 'skip', key?: string) {
    if (!current || busy) return;
    busy = true;
    try {
      // Answering again changes the sample the first answer added (the backend keeps the link).
      await api.answerReview(current.id, action, key);
      given[current.id] = action === 'skip' ? null : action === 'confirm' ? current.state_key : (key ?? null);
      answered(
        action === 'confirm'
          ? `✓ Confirmed ${predicted?.name}`
          : action === 'label'
            ? `✓ Corrected to ${stateInfo(current.sensor, key).name}`
            : 'Skipped',
      );
    } catch (err) {
      toastError(err);
    } finally {
      busy = false;
    }
  }

  async function answerQuestion(action: 'yes' | 'no' | 'skip') {
    const q = current && question(current);
    if (!current || !q || busy) return;
    busy = true;
    try {
      // Answering again replaces the box the first answer taught.
      await api.answerQuestion(current.id, action);
      given[current.id] = action === 'skip' ? null : action;
      const name = labelName(current, q.label);
      answered(action === 'yes' ? `✓ ${name}` : action === 'no' ? `✓ Not ${name}` : 'Skipped');
    } catch (err) {
      toastError(err);
    } finally {
      busy = false;
    }
  }

  /** Was this state (null: Skip) the answer given before? Marks the button when an item is opened again. */
  const chosen = (key: string | null) => editing !== null && !!current && current.id in given && given[current.id] === key;

  function onkey(e: KeyboardEvent) {
    if (!current || isReading(current) || (e.target as HTMLElement).closest('input, select, textarea')) return;
    if (e.key === 'Escape') editing = null;
    else if (question(current)) {
      if (e.key === 'Enter') answerQuestion('yes');
      else if (e.key.toLowerCase() === 'n') answerQuestion('no');
      else if (e.key.toLowerCase() === 's') answerQuestion('skip');
    }
    else if (e.key === 'Enter') answer('confirm');
    else if (e.key.toLowerCase() === 's') answer('skip');
    else {
      const state = current.sensor.states[Number(e.key) - 1];
      if (state) answer(state.key === current.state_key ? 'confirm' : 'label', state.key);
    }
  }
</script>

<svelte:window onkeydown={onkey} />

{#snippet header(item: ReviewItem)}
  {@const reason = item.review_reason ? REVIEW_REASONS[item.review_reason] : null}
  <div class="row wrap">
    <span class="mono small muted">{shown + 1} / {items?.length}</span>
    <strong>{item.sensor.name}</strong>
    {#if reason}<span class="chip {reason.tone}" title={reason.help}>{reason.label}</span>{/if}
    <span class="spacer"></span>
    <span class="xsmall faint">{dateTime(item.created_at)}</span>
  </div>
  {#if editing !== null}
    <div class="row wrap again">
      <span class="small">Answered: <span style:color={TONE_COLOR.ok}>{answers[item.id]}</span> — answer again to change it.</span>
      <span class="spacer"></span>
      <button class="btn sm" onclick={() => (editing = null)}>Back to the queue</button>
    </div>
  {/if}
{/snippet}

<div class="page">
  <div class="layout">
    <aside class="col">
      <div class="col" style="gap:4px">
        <h1>Review</h1>
        <p class="small muted">
          Frames the AI was unsure about, objects that may be one of your own labels, and readings that were rejected.
          Answers for state sensors and own labels are learned from — a few clicks here help the most. Answers for
          readings show how reliable they are; the reader does not learn from them.
        </p>
      </div>
      {#if waiting.length}
        <div class="card pad col waiting">
          <span class="small muted">Waiting per sensor</span>
          {#each waiting as w (w.id)}
            <div class="row wait-row">
              <span class="col" style="gap:0;min-width:0">
                <strong class="small name">{w.name}</strong>
                <span class="xsmall faint">{w.count} waiting</span>
              </span>
              <span class="spacer"></span>
              <ConfirmButton class="btn sm" disabled={busy} confirmLabel="Press again" onconfirm={() => dismissAll(w)}>
                Dismiss all
              </ConfirmButton>
            </div>
          {/each}
          <span class="xsmall faint">Dismiss all takes them out of the queue. Answers already given and the counts in Quality are kept.</span>
        </div>
      {/if}
      {#if items?.length}
        <ol>
          {#each items as item, i (item.id)}
            {@const reason = item.review_reason ? REVIEW_REASONS[item.review_reason] : null}
            <li class:current={i === shown} class:done={i < index}>
              {#snippet entry()}
                <img src={api.historyImageUrl(item.id, 'thumb')} alt="" loading="lazy" />
                <span class="col" style="gap:2px;min-width:0">
                  <strong class="small">{item.sensor.name}</strong>
                  <span class="xsmall" style:color={i < index ? TONE_COLOR.ok : TONE_COLOR[reason?.tone ?? 'muted']}>
                    {answers[item.id] ?? `${reason?.label ?? ''} · ${summary(item)}`}
                  </span>
                </span>
              {/snippet}
              <!-- Answered items open again to change the answer; the next waiting one leads back. -->
              {#if i <= index}
                <button type="button" class="entry" title={i < index ? 'Change this answer' : undefined} onclick={() => (editing = i < index ? i : null)}>
                  {@render entry()}
                </button>
              {:else}
                <div class="entry">{@render entry()}</div>
              {/if}
            </li>
          {/each}
        </ol>
        {#if total > items.length}<p class="xsmall faint">{total - items.length} more after these.</p>{/if}
      {/if}
    </aside>

    <section class="card pad main">
      {#if items === null}
        <p class="muted">Loading…</p>
      {:else if current && isReading(current)}
        {@const d = readingDetail(current)}
        {@render header(current)}
        <div class="frame">
          <RoiEditor src={api.historyImageUrl(current.id)} roi={readingRoi(current, current.sensor)} />
        </div>
        <div class="col center">
          <p class="question">
            Did it read <span class="mono">“{d.text || '—'}”</span>{d.value ? ` (${d.value}${unitOf(current) ? ` ${unitOf(current)}` : ''})` : ''} right?
          </p>
          <p class="small muted">
            {#if d.reason}Rejected: {REJECT_REASONS[d.reason] ?? d.reason} — the last value was kept.
            {:else}Accepted and published. A random check to find misreads that pass every test.{/if}
          </p>
          {#key current.id}
            <ReadingVerdict item={current} reading={current.sensor.reading} unit={unitOf(current)} large onanswer={readingAnswered} />
          {/key}
        </div>
      {:else if current && question(current)}
        {@const q = question(current)!}
        {@const name = labelName(current, q.label)}
        {@render header(current)}
        <div class="frame">
          <RoiEditor src={api.historyImageUrl(current.id)} roi={current.sensor.roi}>
            <DetectionBoxes detections={[{ key: q.detected, score: current.confidence, box: q.box }]} classes={[q.detected]} />
          </RoiEditor>
        </div>
        <div class="col center">
          <p class="question">
            Is this <strong>{name}</strong>?
            <span class="small muted">It looks {pct(q.similarity)} like what you taught as {name} — not quite enough to be sure.</span>
          </p>
          <div class="row wrap center-row">
            <button class="btn primary lg" class:chosen={chosen('yes')} disabled={busy} onclick={() => answerQuestion('yes')}>
              <Icon name="check" /> Yes, {name}
            </button>
            <button class="btn lg" class:chosen={chosen('no')} disabled={busy} onclick={() => answerQuestion('no')}>
              No, another {objectName(q.detected).toLowerCase()}
            </button>
            <button class="btn lg ghost" class:chosen={chosen(null)} disabled={busy} onclick={() => answerQuestion('skip')}>Skip</button>
          </div>
          <span class="xsmall faint">Your answer is taught: boxes like this one are told apart from now on.</span>
          <span class="xsmall faint kbd-only">Enter = yes · N = no · S = skip</span>
        </div>
      {:else if current && predicted}
        {@render header(current)}
        <div class="frame">
          <RoiEditor src={api.historyImageUrl(current.id)} roi={current.sensor.roi} />
        </div>
        <div class="col center">
          <p class="question">
            Is this <span style:color={predicted.color}>{predicted.name}</span>?
            <span class="small muted">The AI was {pct(current.confidence)} sure.</span>
          </p>
          <div class="row wrap center-row">
            <button class="btn primary lg" class:chosen={chosen(current.state_key)} disabled={busy} onclick={() => answer('confirm')}>
              <Icon name="check" /> Yes, {predicted.name}
            </button>
            <span class="small faint">No, it is</span>
            {#each others as s (s.key)}
              <button
                class="btn lg state"
                class:chosen={chosen(s.key)}
                style:border-left-color={s.color}
                disabled={busy}
                onclick={() => answer('label', s.key)}>{s.name}</button
              >
            {/each}
            <button class="btn lg ghost" class:chosen={chosen(null)} disabled={busy} onclick={() => answer('skip')}>Skip</button>
          </div>
          <span class="xsmall faint kbd-only">Enter = yes · 1–9 = pick state · S = skip</span>
        </div>
      {:else}
        <div class="col center empty">
          <span class="done-icon"><Icon name="check" size={30} strokeWidth={2} /></span>
          <h2>All caught up</h2>
          <p class="muted">Your answers were added to the datasets and the models retrained. New uncertain frames will show up here.</p>
          <button class="btn" onclick={load}><Icon name="refresh" size={14} /> Check again</button>
        </div>
      {/if}
    </section>
  </div>
</div>

<style>
  .layout {
    display: grid;
    grid-template-columns: 320px minmax(0, 1fr);
    gap: var(--space-6);
    align-items: start;
  }
  ol {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .waiting {
    gap: var(--space-2);
  }
  .wait-row {
    gap: var(--space-3);
  }
  .name {
    overflow-wrap: anywhere;
  }
  .entry {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    width: 100%;
    padding: var(--space-2);
    border-radius: var(--radius-lg);
    border: 1px solid transparent;
    background: none;
    color: inherit;
    font: inherit;
    text-align: left;
  }
  button.entry {
    cursor: pointer;
  }
  li.current .entry {
    background: var(--c-surface-3);
    border-color: var(--c-border-dashed);
  }
  li.done:not(.current) .entry {
    opacity: 0.6;
  }
  li.done:not(.current) .entry:hover {
    opacity: 0.85;
  }
  .again {
    gap: var(--space-3);
    padding: var(--space-2) var(--space-3);
    border-radius: var(--radius-md);
    background: var(--c-surface-3);
  }
  .chosen {
    outline: 2px solid var(--c-accent);
    outline-offset: 2px;
  }
  li img {
    width: 88px;
    aspect-ratio: 16 / 9;
    object-fit: cover;
    border-radius: var(--radius-sm);
    flex-shrink: 0;
  }
  .main {
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
  }
  .frame {
    width: min(100%, 800px);
    align-self: center;
    border-radius: var(--radius-lg);
    overflow: hidden;
  }
  .center {
    align-items: center;
    text-align: center;
  }
  .center-row {
    justify-content: center;
  }
  .question {
    font: 600 22px var(--font-display);
  }
  .state {
    border-left-width: 4px;
  }
  .empty {
    min-height: 480px;
    justify-content: center;
    gap: var(--space-3);
  }
  .done-icon {
    width: 64px;
    height: 64px;
    border-radius: var(--radius-pill);
    background: var(--c-accent-soft);
    color: var(--c-accent);
    display: flex;
    align-items: center;
    justify-content: center;
  }
  @media (max-width: 1000px) {
    .layout {
      grid-template-columns: minmax(0, 1fr);
    }
    aside ol {
      display: none;
    }
  }
</style>
