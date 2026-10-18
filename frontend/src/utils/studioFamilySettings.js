/** Server capabilities own the controls; the fallback only supports older Z-Image payloads. */
export function supportsNegativePrompt(capabilities, family) {
  return typeof capabilities?.negative_prompt === 'boolean'
    ? capabilities.negative_prompt : family === 'zimage';
}

/** Keep an explicit missing model visible instead of rendering on another base. */
export function modelSelectionError(models, selected) {
  if (!Array.isArray(models)) return null;
  const available = new Set(models.map((m) => m.value));
  const missing = (selected || []).filter((value) => !available.has(value));
  if (!missing.length) return null;
  return `Base model unavailable: ${missing.map((value) => value || 'configured default').join(', ')}. Choose an available base model.`;
}

/** A configured base is still the default when its file is missing; never select a replacement. */
export function defaultModelSelection(models, configuredDefault) {
  if (configuredDefault != null) return [configuredDefault];
  return models?.length ? [models[0].value] : [];
}

/** Families installed by the shared Studio model card in Engines settings. */
export function studioModelSettingsLink(family) {
  return ['flux', 'anima', 'qwenimage21', 'moss'].includes(family)
    ? '#/settings?section=engines&focus=studio-models' : null;
}

// These are the families that could own the old, unscoped comparison preferences.
// New families must start from their server defaults, never an old Turbo session.
const LEGACY_AXIS_FAMILIES = new Set(['zimage', 'sdxl', 'krea', 'flux2klein']);
const LEGACY_AXIS_OWNER = 'studioComp_legacyAxisFamily';

export function readComparisonAxis(storage, key, family) {
  try {
    let saved = storage.getItem(`${key}_${family}`);
    if (saved == null && LEGACY_AXIS_FAMILIES.has(family)) {
      const owner = storage.getItem(LEGACY_AXIS_OWNER);
      if (owner == null || owner === family) {
        saved = storage.getItem(key);
        if (saved != null) storage.setItem(LEGACY_AXIS_OWNER, family);
      }
    }
    const value = JSON.parse(saved || 'null');
    return Array.isArray(value) && value.length && value.every(Number.isFinite) ? value : null;
  } catch { return null; }
}

export function writeComparisonAxis(storage, key, family, value) {
  try { storage.setItem(`${key}_${family}`, JSON.stringify(value)); }
  catch { /* Storage is optional in private browsing. */ }
}
