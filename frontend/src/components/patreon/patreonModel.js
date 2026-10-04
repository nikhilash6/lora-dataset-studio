// Pure helpers for the Patreon membership surfaces (Library card, install
// review, Updates card). The backend already pins every address; these checks
// repeat the pin before an address becomes a clickable link.

export const PATREON_AUTHORIZE_PREFIX = 'https://www.patreon.com/oauth2/authorize?';

export const PATREON_ACCESS_SUMMARY = 'New features, exclusive plugins and LDS updates are reserved for '
  + 'Patreon members (LDS Member and LDS Pro). The version you have stays free and keeps working.';

export const PATREON_PRO_SUMMARY = 'LDS Pro members can also request custom plugins.';

// Patreon offers no lookup by user: a linked non-member becomes active through
// the creator's webhook, or by connecting again.
export const JUST_JOINED_HINT = 'Just joined? Press Connect Patreon again if your membership does not appear.';

const TIERS = Object.freeze({ member: 'LDS Member', pro: 'LDS Pro' });

export function tierLabel(tier) {
  return TIERS[tier] || '';
}

function httpsUrl(value) {
  if (typeof value !== 'string' || !value || /[\s\\]/.test(value)) return null;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password && !url.port && !url.hash ? url : null;
  } catch {
    return null;
  }
}

export function safeAuthorizeUrl(value) {
  const url = httpsUrl(value);
  return url && value.startsWith(PATREON_AUTHORIZE_PREFIX) && url.host === 'www.patreon.com'
    && url.pathname === '/oauth2/authorize' ? value : '';
}

export function safeJoinUrl(value) {
  const url = httpsUrl(value);
  return url && url.host === 'www.patreon.com' ? value : '';
}

export function safeRequestUrl(value) {
  return httpsUrl(value) ? value : '';
}

/* One line for the membership state; `access` is the backend's
   {status, patreon} summary (status: ready | not_connected | not_configured |
   unavailable, or not_permitted when the request lacked administration). */
export function membershipLabel(access) {
  const patreon = access?.patreon;
  if (access?.status === 'not_configured') return 'Patreon access is not available on this installation yet.';
  if (access?.status === 'not_permitted') {
    return 'Patreon membership is managed from this computer, or with the plugin admin token.';
  }
  if (!patreon) return 'Patreon could not be reached. Installed plugins and this version of LDS keep working.';
  if (!patreon.linked) return 'Patreon is not connected to this installation.';
  if (patreon.status === 'active') return `Active Patreon membership${tierLabel(patreon.tier) ? ` · ${tierLabel(patreon.tier)}` : ''}`;
  if (patreon.status === 'verification_required') {
    return 'Patreon could not confirm your membership recently. Press Refresh; reconnect Patreon if this persists.';
  }
  return 'Patreon is connected, but no active LDS membership was found.';
}

/* Which actions to offer. Connect (again) and Join while not an active member,
   Disconnect while linked, custom requests for active Pro members. */
export function patreonActions(access) {
  const patreon = access?.patreon;
  const ready = Boolean(patreon) && access?.status !== 'not_configured' && access?.status !== 'unavailable';
  const linked = Boolean(ready && patreon.linked);
  const active = linked && patreon.status === 'active';
  return {
    connect: ready && !active,
    reconnect: linked && !active,
    join: ready && !active && Boolean(safeJoinUrl(patreon.join_url)),
    disconnect: linked,
    request: active && patreon.tier === 'pro' && Boolean(safeRequestUrl(patreon.request_url)),
  };
}

export function isPatreonRelease(release) {
  return release?.price?.provider === 'patreon';
}
