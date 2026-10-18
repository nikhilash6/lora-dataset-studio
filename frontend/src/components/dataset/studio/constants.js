import { FAMILY_IDS, familyLabel } from '../../../utils/familyBadges.js';

// Shared LoRA Test Studio constants. Zero means the base model with LoRA off, a useful control
// column reached by low strengths. The always-visible base row spans 0-2.0, with fine steps below
// 1.0 and coarser steps above.
export const STRENGTH_CHOICES = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0];
// Extended row, revealed behind « + » (progressive disclosure): above 2.0 you are
// looking for the LoRA's over-cook / breaking point, so coarser steps up to the
// server-accepted ceiling. Kept out of the always-on row to avoid clutter.
// ⚠️ The last chip IS the server ceiling (lora_test_studio.MAX_LORA_STRENGTH,
// 5.0 since 2026-08-08): a chip above it is a run refused, not a run clamped.
export const STRENGTH_CHOICES_EXTENDED = [2.25, 2.5, 2.75, 3.0, 3.5, 4.0, 4.5, 5.0];
// Negative row, revealed behind « − » (same disclosure pattern, mirrored): a
// negative strength pulls the LoRA the OTHER way — how you test the negative
// pole of a slider LoRA (ours or any downloaded one). Floor mirrors the server
// bound in lora_test_studio.build_matrix ([MIN_LORA_STRENGTH, MAX_LORA_STRENGTH]).
export const STRENGTH_CHOICES_NEGATIVE = [-2.0, -1.5, -1.0, -0.75, -0.5, -0.25];
export const DEFAULT_STRENGTHS = [0.7, 0.85, 1.0];
// Training-family/pipeline labels for the family picker.
export const FAMILY_LABELS = { ...Object.fromEntries(
  FAMILY_IDS.map((family) => [family, family === 'krea' ? 'Krea 2' : familyLabel(family)]),
), moss: 'MOSS-Image' };
