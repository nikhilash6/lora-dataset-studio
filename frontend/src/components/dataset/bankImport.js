/* 🗃 Import from a bank or a dataset — the DECIDABLE part, kept free of JSX so
   `node --test` can run it.

   Two ways in. "Import all kept" picks a whole BANK and lets the server's own
   promote path do the rest (normalize + perceptual dedup against the dataset);
   the picker copies only the images the user clicked, from a bank or from
   another dataset. For the first, what has to be right is the ARITHMETIC and
   the WORDING, because "0 promotable" has two completely different meanings:

     * the bank was never triaged — promote only ever copies KEPT images, so a
       bank full of undecided rows offers zero. The fix is to go triage it.
     * every kept image is ALREADY on this dataset — nothing to do, and the user
       should be told that instead of being sent back to the bank for nothing.

   A bare "nothing to promote" conflates the two and has cost real time before,
   hence the explicit reason codes below. */

/** GET route for the honest per-target count of ONE bank. dataset_id is
 *  REQUIRED: the count is per dataset (an image promoted to ANOTHER dataset
 *  still counts here). Used by the bank page's own promote dialog, which only
 *  ever asks about the bank it has open. */
export const promotableUrl = (bankId, datasetId) =>
  `/api/bank/${Number(bankId)}/promotable?dataset_id=${Number(datasetId)}`;

/** The chooser's ONE request: the bank list, with every bank's promotable count
 *  for this dataset embedded. Asking /promotable per bank instead would cost
 *  1 + N requests to open a panel — the counts are all read from the same table
 *  in one grouped query server-side, so they come back together or not at all. */
export const banksUrl = (datasetId) =>
  `/api/banks?dataset_id=${Number(datasetId)}`;

/** bank id → promotable count, read off a /api/banks?dataset_id= payload.
 *  A row WITHOUT the field is left out (not defaulted to 0): "we don't know" and
 *  "nothing to import" say different things to the user, and bankImportOption
 *  renders the first as "Counting…" rather than a wrong "no kept images". */
export function promotableCounts(banks) {
  const out = {};
  for (const b of banks || []) {
    if (b && b.promotable != null) out[b.id] = Number(b.promotable);
  }
  return out;
}

/** POST route that starts the background promote job. */
export const promoteUrl = (bankId) => `/api/bank/${Number(bankId)}/promote`;

/** Body for "import everything promotable": an EMPTY image_ids means "every kept
 *  image not already on this dataset" (the bank's own no-selection semantics). */
export const promoteAllBody = (datasetId) => ({
  dataset_id: Number(datasetId),
  image_ids: [],
});

/** One row of the bank chooser: what to show, whether it can be imported, and —
 *  when it can't — WHY, in words the user can act on.
 *  `promotable` is the /promotable count, or null/undefined while it loads. */
export function bankImportOption(bank, promotable) {
  const total = Number(bank?.total || 0);
  const keep = Number(bank?.keep || 0);
  const reject = Number(bank?.reject || 0);
  const base = { id: bank?.id, name: bank?.name || '', total, keep };

  if (promotable == null) {
    return { ...base, count: null, ready: false, reason: 'loading', hint: 'Counting…' };
  }
  const count = Number(promotable);
  if (count > 0) {
    return {
      ...base,
      count,
      ready: true,
      reason: null,
      hint: `${count} kept image${count === 1 ? '' : 's'} to import`,
    };
  }
  if (total === 0) {
    return { ...base, count: 0, ready: false, reason: 'empty', hint: 'This bank has no images yet.' };
  }
  if (keep === 0) {
    // Triaged to nothing vs never triaged — both give 0, but only one is a mistake.
    if (reject >= total) {
      return {
        ...base,
        count: 0,
        ready: false,
        reason: 'all-rejected',
        hint: `All ${total} image${total === 1 ? '' : 's'} in this bank are rejected — nothing kept to import.`,
      };
    }
    return {
      ...base,
      count: 0,
      ready: false,
      reason: 'untriaged',
      hint: `No kept images yet — ${total - reject} still undecided. Triage this bank first.`,
    };
  }
  return {
    ...base,
    count: 0,
    ready: false,
    reason: 'already-imported',
    hint: `All ${keep} kept image${keep === 1 ? '' : 's'} are already in this dataset.`,
  };
}

/** The whole chooser, in the order /api/banks returned (newest first).
 *  `counts` maps bank id → promotable count (missing = still loading). */
export function bankImportOptions(banks, counts = {}) {
  return (banks || []).map((b) => bankImportOption(b, counts?.[b?.id]));
}

/** A bank job is "live" while it exists and hasn't finished — same contract the
 *  bank page reads off the embedded snapshot (bank_jobs). */
export const isBankJobLive = (activity) => Boolean(activity && !activity.finished);

/** The embedded job snapshot of one bank inside a /api/banks payload. */
export function bankActivity(banks, bankId) {
  const row = (banks || []).find((b) => Number(b?.id) === Number(bankId));
  return row?.activity || null;
}

/** What to say when a promote job ends: the server's own detail when it has one,
 *  an error when it failed, so the toast never invents a number. */
export function promoteOutcome(activity) {
  if (!activity) return null;
  if (activity.error) return { kind: 'error', text: `Import failed — ${activity.error}` };
  if (activity.cancelled) return { kind: 'info', text: activity.detail || 'Import stopped.' };
  return { kind: 'success', text: activity.detail || 'Import finished.' };
}

/* ---- The picker: images chosen one by one, from a bank or a dataset ---------
   One <select> lists both kinds of source. A bank pages over SQL and serves its
   thumbnails by row id; a dataset arrives whole and serves them by filename. */

/** The library's datasets, for the "Datasets" half of the source chooser. */
export const datasetsUrl = '/api/dataset/list';

/** One dataset's payload, whose `images` the picker offers. */
export const datasetUrl = (datasetId) => `/api/dataset/${Number(datasetId)}`;

/** The chooser's option value, so one select can carry both kinds. */
export const sourceValue = (kind, id) => `${kind}:${Number(id)}`;

/** 'bank:3' → { kind: 'bank', id: 3 }; anything else → null. */
export function parseSource(value) {
  const m = /^(bank|dataset):(\d+)$/.exec(String(value || ''));
  return m ? { kind: m[1], id: Number(m[2]) } : null;
}

/** The OTHER datasets of the library, read off /api/dataset/list: a dataset is
 *  never offered as a source for itself. */
export function datasetSourceOptions(datasets, currentId) {
  return (datasets || [])
    .filter((d) => d && Number(d.id) !== Number(currentId))
    .map((d) => ({
      id: d.id,
      name: d.name || '',
      total: Number(d.images_total || 0),
      kept: Number(d.images_kept || 0),
    }));
}

/** Bank rows per picker page — a bank can hold six figures of images. */
export const PICKER_PAGE = 120;

/** One page of a bank's images for the picker: the kept ones, unless `all`. */
export function bankImagesUrl(bankId, { all = false, offset = 0 } = {}) {
  const qs = new URLSearchParams({ offset: String(offset), limit: String(PICKER_PAGE) });
  if (!all) qs.set('status', 'keep');
  return `/api/bank/${Number(bankId)}/images?${qs}`;
}

/** What the picker offers from a dataset: rows that have a file (a failed
 *  generation has none), the kept ones unless `all`. */
export function pickableDatasetImages(images, { all = false } = {}) {
  return (images || []).filter((img) => img && img.filename && img.status !== 'failed'
    && (all || img.status === 'keep'));
}

/** Promote body for an explicit selection: the bank copies exactly these ids,
 *  whatever their status. */
export const promoteSelectionBody = (datasetId, ids) => ({
  dataset_id: Number(datasetId),
  image_ids: (ids || []).map(Number),
});

/** POST route copying picked images of another dataset into this one. */
export const importFromDatasetUrl = (datasetId) =>
  `/api/dataset/${Number(datasetId)}/import-from-dataset`;

/** Ids per POST. The server accepts up to 200; smaller batches let the button
 *  count the copy as it goes. */
export const DATASET_COPY_BATCH = 50;

export function idBatches(ids, size = DATASET_COPY_BATCH) {
  const out = [];
  for (let i = 0; i < (ids || []).length; i += size) out.push(ids.slice(i, i + size));
  return out;
}

/** The end-of-copy toast, worded like the other dataset imports. */
export function datasetCopyOutcome({ imported = 0, duplicates = 0, failed = 0 } = {}) {
  const parts = [`${imported} imported`];
  if (duplicates) parts.push(`${duplicates} duplicate(s) skipped`);
  if (failed) parts.push(`${failed} could not be copied`);
  return parts.join(' · ');
}
