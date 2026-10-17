// react-frontend/src/components/dataset/studio/LoraPicker.jsx
/**
 * Standalone Studio LoRA picker fetches /api/studio/checkpoints and shows one selectable card per
 * dataset/FAMILY. Each selected card offers checkpoint selection, defaulting to the first/final
 * checkpoint; preselectDataset is checked initially. Multi-family datasets have multiple rows,
 * requiring composite dataset_id:family keys for unique React identity and unambiguous picked
 * state. Every selection/checkpoint change emits onSelectionChange([{dataset_id, checkpoint,
 * lora_label, trigger_word, train_type, family}]). Multiple selections get a badge. One run uses
 * one family: selecting a LoRA disables other families with a tooltip; clearing all selections
 * resets the lock.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { familyBadgeClass, familyLabel } from '../../../utils/familyBadges';
import { useToast } from '../../common/Toast';

// Entry family is supplied by the backend; train_type is the backward-compatible alias.
const famOf = (l) => l.family || l.train_type || 'zimage';
// Composite dataset/family row key provides stable identity in picked.
const keyOf = (l) => `${l.dataset_id}:${famOf(l)}`;


export default function LoraPicker({ preselectDataset, preselectFamily = null, onSelectionChange }) {
  const toast = useToast();
  const [loras, setLoras] = useState([]);
  const [loading, setLoading] = useState(true);
  // Map datasetId:family to the selected checkpoint filename; presence means checked.
  const [picked, setPicked] = useState({});
  // Preselect only once, not again on every refetch.
  const preselectedRef = useRef(false);
  // Restore saved selection once, after the first fetch.
  const restoredRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    fetch('/api/studio/checkpoints', { credentials: 'include' })
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((d) => {
        if (cancelled) return;
        setLoras(d.loras || []);
        setLoading(false);
      })
      .catch(() => {
        if (cancelled) return;
        setLoading(false);
        toast.error('Could not load the LoRA list');
      });
    return () => { cancelled = true; };
  }, [toast]);

  // After loading, preselect the URL-selected dataset's first row. Its default checkpoint is first
  // in the list, meaning final on the backend.
  useEffect(() => {
    if (preselectedRef.current || !preselectDataset || !loras.length) return;
    const target = loras.find((l) => (
      String(l.dataset_id) === String(preselectDataset)
      && (!preselectFamily || famOf(l) === preselectFamily)
    ));
    if (target && target.checkpoints?.length) {
      preselectedRef.current = true;
      setPicked({ [keyOf(target)]: target.checkpoints[0].filename });
    }
  }, [preselectDataset, preselectFamily, loras]);

  // Restore the LAST visit's selected LoRAs/checkpoints across reloads, as requested on
  // 2026-07-03. Explicit ?dataset= URL selection takes priority. Restore only entries still
  // present in the fresh list, ignoring deleted LoRAs, and only one family to preserve the
  // one-run/one-family rule.
  useEffect(() => {
    if (restoredRef.current || !loras.length) return;
    restoredRef.current = true;
    if (preselectDataset) return;
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem('studioPicked_v1') || '{}') || {}; } catch { /* ignore */ }
    const valid = {};
    let fam = null;
    for (const l of loras) {
      const k = keyOf(l);
      if (saved[k] == null) continue;
      if (fam === null) fam = famOf(l);
      if (famOf(l) !== fam) continue;
      const cps = (l.checkpoints || []).map((c) => c.filename);
      valid[k] = cps.includes(saved[k]) ? saved[k] : (cps[0] || '');
    }
    if (Object.keys(valid).length) setPicked(valid);
  }, [loras, preselectDataset]);

  // Persist changes only AFTER restoration; otherwise the initial {} would overwrite the saved
  // selection before reading it.
  useEffect(() => {
    if (!restoredRef.current && !preselectedRef.current) return;
    try { localStorage.setItem('studioPicked_v1', JSON.stringify(picked)); } catch { /* ignore */ }
  }, [picked]);

  // Emit normalized selection to the parent on every change. Include train_type AND family from
  // the ROW, not the dataset's train_type, so StudioShell fetches correct bases and the backend
  // can validate a single family.
  const selection = useMemo(() => {
    const out = [];
    for (const l of loras) {
      const cp = picked[keyOf(l)];
      if (cp) out.push({
        dataset_id: l.dataset_id,
        checkpoint: cp,
        lora_label: l.lora_label,
        // Expose each combined LoRA's trigger to LoraStackPanel; the backend injects all triggers
        // into the prompt.
        trigger_word: l.trigger_word || null,
        train_type: famOf(l),
        family: famOf(l),
      });
    }
    return out;
  }, [loras, picked]);

  // Run family is that of the first selected LoRA, or null when none is selected.
  const runType = selection.length > 0 ? selection[0].family : null;

  useEffect(() => { onSelectionChange?.(selection); }, [selection, onSelectionChange]);

  const toggle = (l) => {
    // Do not allow selection of a LoRA from a different family than the current run.
    const k = keyOf(l);
    if (runType !== null && famOf(l) !== runType && picked[k] == null) return;
    setPicked((cur) => {
      const next = { ...cur };
      if (next[k] != null) delete next[k];
      else next[k] = l.checkpoints?.[0]?.filename || '';
      return next;
    });
  };
  const setCheckpoint = (key, filename) =>
    setPicked((cur) => ({ ...cur, [key]: filename }));

  const count = selection.length;

  return (
    <div data-probe-panel="picker" className="flex flex-col gap-2 rounded-lg border border-border bg-surface p-3">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-content-muted text-[0.6875rem] uppercase">LoRA to test (optional)</span>
        <button type="button" onClick={() => setPicked({})} aria-pressed={count === 0}
          disabled={loading}
          className={`rounded-lg border px-2 py-1 text-xs ${count === 0
            ? 'border-primary/40 bg-primary/10 text-content' : 'border-border text-content-muted'}`}>
          Base model only
        </button>
        {/*
         * This badge used to say Comparison for two or more LoRAs, which became inaccurate once
         * Blend was a separate mode selected below in LoraStackPanel. State only the FACT of
         * multiple selections, not what will be done with them.
         */}
        {count >= 2 && (
          <span className="px-2 py-0.5 rounded-full text-[0.625rem] font-semibold border border-border-strong bg-surface-raised text-content">
            Multi-LoRA ({count})
          </span>
        )}
        <span className="ml-auto text-content-subtle text-[0.6875rem]">{count} checked</span>
      </div>

      {loading ? (
        <p className="text-content-subtle text-sm">Loading LoRA…</p>
      ) : loras.length === 0 ? (
        <p className="text-content-subtle text-sm">
          No trained LoRA available. You can generate with a base model below.
        </p>
      ) : (
        <div className="max-h-72 overflow-auto flex flex-col gap-1.5">
          {loras.map((l) => {
            const k = keyOf(l);
            const on = picked[k] != null;
            // Family lock: disable when another family is already selected.
            const lType = famOf(l);
            const locked = runType !== null && !on && lType !== runType;
            return (
              <div key={k}
                className={`flex flex-col gap-1 rounded-lg border px-2.5 py-2 ${
                  on ? 'border-primary/40 bg-primary/10'
                  : locked ? 'border-border bg-surface-raised opacity-50'
                  : 'border-border bg-surface-raised'
                }`}>
                <button type="button" onClick={() => toggle(l)} aria-pressed={on}
                  disabled={locked}
                  title={locked ? 'One run = one family only (deselect all to switch family)' : undefined}
                  className={`flex items-center gap-2 text-left ${locked ? 'cursor-not-allowed' : ''}`}>
                  <span aria-hidden className={`inline-flex w-4 h-4 shrink-0 items-center justify-center rounded border text-[0.625rem] ${on ? 'border-primary bg-primary/30 text-white' : 'border-border text-transparent'}`}>
                    ✓
                  </span>
                  <span className="text-content font-medium text-sm truncate" title={l.lora_label}>
                    {l.lora_label}
                  </span>
                  {l.trigger_word && (
                    <code className="px-1.5 py-0.5 rounded border border-indigo-400/40 bg-indigo-500/10 text-indigo-300 text-[0.625rem] font-semibold">
                      {l.trigger_word}
                    </code>
                  )}
                  {/*
                   * Badge EVERY family, including Z-Image: multi-family datasets have one row per
                   * pipeline, so unbadged rows would be ambiguous. Give each family a distinct
                   * color.
                   */}
                  <span className={`px-1.5 py-0.5 rounded border text-[0.5625rem] font-semibold uppercase ${familyBadgeClass(lType)}`}>
                    {familyLabel(lType)}
                  </span>
                  <span className="ml-auto text-content-subtle text-[0.625rem] truncate max-w-[120px]" title={l.dataset_name}>
                    {l.dataset_name}
                  </span>
                </button>
                {on && l.checkpoints?.length > 1 && (
                  <label className="flex items-center gap-2 text-content-muted text-[0.6875rem] pl-6">
                    <span className="whitespace-nowrap">Checkpoint:</span>
                    <select value={picked[k] || ''}
                      onChange={(e) => setCheckpoint(k, e.target.value)}
                      aria-label={`Checkpoint for ${l.lora_label}`}
                      className="flex-1 min-w-0 rounded border border-border bg-app/60 px-1.5 py-0.5 text-content">
                      {l.checkpoints.map((c) => (
                        <option key={c.filename} value={c.filename}>{c.label}</option>
                      ))}
                    </select>
                  </label>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
