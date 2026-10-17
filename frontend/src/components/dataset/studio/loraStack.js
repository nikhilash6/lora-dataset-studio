// react-frontend/src/components/dataset/studio/loraStack.js
/**
 * PURE Test Studio Blend-stack logic extracted from JSX for node --test. Canvas uses the SAME
 * module through utils/canvasGeneration, sharing clamps and the at-least-two-LoRAs/one-family
 * rule. The visible label changed from Combine to Blend on 2026-08-03; mode values, API keys and
 * exports intentionally keep combine because they are persisted or public. compare tests each
 * selected LoRA ALONE in its column across strengths. combine loads them TOGETHER at individual
 * weights and removes the irrelevant strengths axis from UI/payload. Store weights independently
 * of LoraPicker selection in a dataset_id:checkpoint map so other LoRAs being unchecked/rechecked
 * or checkpoints changing do not discard them.
 */

/* Extension spelled out: this module is imported directly by `node --test`,
   whose ESM resolver does not add it the way Vite does. */
import { runCost } from './runCost.js';

export const COMBINE_MIN_WEIGHT = 0;
/*
 * Blend-weight ceiling. The old 2.0 limit was a comfort choice, not a ComfyUI restriction, and
 * prevented useful weak-LoRA, dominant-style and strong-slider tests. The ceiling is now 5.0;
 * larger values become noise and invalid input still needs a backend-compatible clamp. Keep
 * IDENTICAL to backend/app/services/lora_test_studio.COMBINE_MAX_WEIGHT because both sides clamp:
 * differing limits would make displayed weights disagree with rendered ones. Values above 2 are
 * reachable through slider AND keyboard, with hundredth precision, avoiding excessive 0.05 slider
 * steps from 1 to 5.
 */
export const COMBINE_MAX_WEIGHT = 5;
const COMBINE_DEFAULT_WEIGHT = 1;

/**
 * Parse a KEYBOARD weight and clamp it, or return null while input is not meaningful. null means
 * leave the weight untouched, not failure: empty text, a lone minus and partial 1. input must not
 * jump the slider to zero or one with each character. Return only finite clamped values.
 */
export function clampBlendWeight(raw) {
  const n = Number(raw);
  if (raw === '' || raw == null || !Number.isFinite(n)) return null;
  return Math.round(Math.min(COMBINE_MAX_WEIGHT, Math.max(COMBINE_MIN_WEIGHT, n)) * 100) / 100;
}

/* Stable selected-LoRA key in the weight map. */
export const stackKey = (sel) => `${sel?.dataset_id}:${sel?.checkpoint}`;

/* Selected weight: clamp from 0 to COMBINE_MAX_WEIGHT, round to hundredths, default to 1. */
export function stackWeight(weights, sel) {
  const raw = Number((weights || {})[stackKey(sel)]);
  if (!Number.isFinite(raw)) return COMBINE_DEFAULT_WEIGHT;
  return Math.round(Math.min(COMBINE_MAX_WEIGHT, Math.max(COMBINE_MIN_WEIGHT, raw)) * 100) / 100;
}

/**
 * Combine requires at least two LoRAs from the SAME family. Return null when valid, otherwise the
 * English message to display. The backend also rejects invalid selection, but users need to know
 * BEFORE spending GPU time.
 */
export function combineBlocker(selection) {
  const sel = selection || [];
  if (sel.length < 2) return 'Check at least two LoRAs to blend them.';
  const families = [...new Set(sel.map((s) => s.family || s.train_type || 'zimage'))];
  if (families.length > 1) {
    return `Blending needs one family: ${families.join(' + ')} use different base `
      + 'models and workflows. Uncheck one of them.';
  }
  return null;
}

/**
 * Build POST /api/studio/run selections. Combine entries carry weight; backend chains extra LoRAs
 * in one graph and injects ALL triggers. Comparison preserves the old payload without weight so
 * existing runs are unchanged.
 */
export function buildSelectionsPayload(selection, { combine = false, weights = {}, sets = {} } = {}) {
  return (selection || []).map((s) => {
    if (!combine) return { dataset_id: s.dataset_id, checkpoint: s.checkpoint };
    const list = stackWeightList(weights, sets, s);
    return {
      dataset_id: s.dataset_id,
      checkpoint: s.checkpoint,
      // Send scalar weight ALONGSIDE weights. Git-pull updates can temporarily run a new frontend
      // against a backend without sweep support; that backend renders the leading weight,
      // producing one correct image rather than N, instead of breaking.
      weight: list[0],
      weights: list,
    };
  });
}

/*
 * BLEND SWEEP: checked weights per LoRA form a Cartesian product of combinations, one cell each in
 * ONE run, replacing separate launches to compare weight assignments. No checked weights means the
 * slider governs with existing hundredth precision and off-grid values. Any checks make the
 * CHECKBOXES authoritative; one check still produces one configuration. No hard cap by design,
 * matching server build_matrix: execution is serial and users see count/time first. Like strength
 * sweeps, exceeding BLEND_WARN_CELLS warns about real cost without rejecting. A future hard-cap
 * policy would use this single constant as a bound instead of a warning threshold.
 */

/**
 * Checkbox grid below each slider uses round values covering useful blend weights; the slider
 * still supports off-grid values.
 */
export const BLEND_WEIGHT_CHIPS = [0.4, 0.6, 0.8, 1.0];

/**
 * Image-count WARNING threshold, never rejection. Matches backend MAX_TEST_IMAGES, previously only
 * reported to the frontend without enforcement.
 */
export const BLEND_WARN_CELLS = 24;

/**
 * Checked weights for a selection: clamp, round, deduplicate and sort. Empty means no boxes
 * checked.
 */
export function stackWeightSet(sets, sel) {
  const raw = (sets || {})[stackKey(sel)];
  if (!Array.isArray(raw)) return [];
  const out = [];
  for (const v of raw) {
    const n = Number(v);
    if (!Number.isFinite(n)) continue;
    const w = Math.round(Math.min(COMBINE_MAX_WEIGHT, Math.max(COMBINE_MIN_WEIGHT, n)) * 100) / 100;
    if (!out.includes(w)) out.push(w);
  }
  return out.sort((a, b) => a - b);
}

/**
 * Weights this LoRA actually sweeps: checked values if any, otherwise its slider value. Never
 * empty; each LoRA contributes at least one weight.
 */
export function stackWeightList(weights, sets, sel) {
  const chosen = stackWeightSet(sets, sel);
  return chosen.length ? chosen : [stackWeight(weights, sel)];
}

/**
 * Cartesian product in selection order, with the LAST LoRA varying fastest, like table reading
 * across the second LoRA while holding the first fixed. Each combination is a weight array aligned
 * with selection.
 */
export function blendCombinations(selection, { weights = {}, sets = {} } = {}) {
  const lists = (selection || []).map((s) => stackWeightList(weights, sets, s));
  if (!lists.length) return [];
  return lists.reduce((acc, list) => acc.flatMap((combo) => list.map((w) => [...combo, w])), [[]]);
}

/** Number of configurations to render, excluding seeds and the batch axis. */
export function blendConfigCount(selection, opts = {}) {
  return (selection || []).length
    ? (selection || []).reduce(
      (n, s) => n * stackWeightList(opts.weights || {}, opts.sets || {}, s).length, 1)
    : 0;
}

const fmtWeight = (w) => {
  const n = Number(w);
  if (!Number.isFinite(n)) return '?';
  return String(Math.round(n * 100) / 100);
};

/**
 * Label ONE combination on its cell, such as subject 0.8 x style 0.6. Names come from
 * selection.lora_label and weights from the combination. Both surfaces provide lora_label, so
 * build labels once here.
 */
export function blendComboLabel(selection, combo) {
  return (selection || [])
    .map((s, i) => `${s.lora_label || s.checkpoint || `LoRA ${i + 1}`} ${fmtWeight((combo || [])[i])}`)
    .join(' × ');
}

/**
 * Describe launch COST and when to warn before clicking. warn NEVER blocks; it changes color and
 * displays estimates.
 */
export function blendSweepCost({ configCount, count = 1, batchMult = 1,
  secondsPerImage = null }) {
  const cells = Math.max(0, Number(configCount) || 0)
    * Math.max(1, Number(count) || 0) * Math.max(1, Number(batchMult) || 1);
  // Use the same estimate as SeedControls: conflicting durations for one launch would be worse
  // than none. Both use runCost and this machine's MEASURED pace when available. The old universal
  // 12-second figure came from a 4090 and was five times wrong on a slow card.
  const cost = runCost(cells, secondsPerImage);
  return {
    configs: Math.max(0, Number(configCount) || 0),
    cells,
    warn: cells > BLEND_WARN_CELLS,
    measured: cost.measured,
    label: cost.label,
    minutes: Math.ceil(cost.seconds / 60),
  };
}

/**
 * Cell count announced BEFORE launch. A stack is one configuration unless checked weights yield
 * configCount Cartesian combinations; total is configCount x count x batchMult. Missing
 * configCount defaults to one, preserving pre-checkbox behavior.
 */
/*
 * axisTotal multiplies the render axes CFG, steps and second pass now offered in comparison/blend
 * too. Default one preserves previous counts when none are swept.
 */
/*
 * promptCount adds the prompt-batch axis: each configuration renders once per selected prompt.
 * Keep it separate from axisTotal because tooltips name axes individually; hiding prompts under
 * CFG/steps would be inaccurate. Default one, no batch, preserves previous counts.
 */
export function cellCount({ selectionCount, strengthCount, count, batchMult = 1,
  combine = false, configCount = 1, axisTotal = 1, promptCount = 1, baseOnly = false }) {
  const n = Math.max(0, Number(count) || 0);
  const mult = Math.max(1, Number(batchMult) || 1);
  const axes = Math.max(1, Number(axisTotal) || 1);
  const prompts = Math.max(1, Number(promptCount) || 1);
  if (baseOnly) return n * mult * axes * prompts;
  if (combine) {
    return selectionCount >= 2
      ? Math.max(0, Number(configCount) || 0) * n * mult * axes * prompts : 0;
  }
  return Math.max(0, selectionCount) * Math.max(0, strengthCount) * n * mult * axes * prompts;
}
