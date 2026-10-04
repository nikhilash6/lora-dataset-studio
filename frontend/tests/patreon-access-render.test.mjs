import assert from 'node:assert/strict'
import test from 'node:test'

import { createElement, render } from './support/mountJsx.mjs'

// The JSX loader is registered while mountJsx is evaluated, so these imports
// stay dynamic (see support/mountJsx.mjs).
const { PatreonAccessActions } = await import('../src/components/patreon/PatreonAccess.jsx')
const { default: PatreonUpdates } = await import('../src/components/patreon/PatreonUpdates.jsx')
const { InstallPlan } = await import('../src/pages/store/Catalog.jsx')
const { default: PluginCard } = await import('../src/pages/store/PluginCard.jsx')
const { default: SupportProjectNotice } = await import('../src/components/common/SupportProjectNotice.jsx')
const { MemoryRouter } = await import('react-router')

const JOIN = 'https://www.patreon.com/c/example'
const UNLINKED = { status: 'not_connected', patreon: { linked: false, status: 'inactive', tier: null,
  valid_until: null, join_url: JOIN, request_url: null } }
const PRO = { status: 'ready', patreon: { linked: true, status: 'active', tier: 'pro', valid_until: '2026-11-04T00:00:00Z',
  join_url: JOIN, request_url: 'https://requests.example.test/form' } }
const state = (access, extra = {}) => ({ access, busy: false, error: '', authorizeUrl: '',
  load() {}, connect() {}, disconnect() {}, ...extra })

test('membership actions render in every state', () => {
  const unlinked = render(PatreonAccessActions, { state: state(UNLINKED) })
  assert.match(unlinked, />Connect Patreon</)
  assert.match(unlinked, /Join on Patreon/)
  assert.match(unlinked, /Just joined\?/)
  const pro = render(PatreonAccessActions, { state: state(PRO) })
  assert.match(pro, /Request a custom plugin/)
  assert.match(pro, />Disconnect</)
  assert.doesNotMatch(pro, /Connect Patreon|Join on Patreon/)
  const lapsed = render(PatreonAccessActions, { state: state({ status: 'ready',
    patreon: { ...UNLINKED.patreon, linked: true } }) })
  assert.match(lapsed, /Connect Patreon again/)
  const pending = render(PatreonAccessActions, {
    state: state(UNLINKED, { authorizeUrl: 'https://www.patreon.com/oauth2/authorize?state=x' }) })
  assert.match(pending, /href="https:\/\/www\.patreon\.com\/oauth2\/authorize\?state=x"[^>]*>Continue on Patreon/)
})

test('the Patreon block of the Updates card renders each channel state', () => {
  const ready = { status: 'ready', update_available: true, install_mode: 'git',
    release: { version: '2026.10.20', notes: ['Members feature'] } }
  assert.match(render(PatreonUpdates, { patreon: { ...ready, can_apply: true, entitled: true } }), /Update &amp; restart/)
  assert.match(render(PatreonUpdates, { patreon: { ...ready, can_apply: true, entitled: true, install_mode: 'pinokio' } }),
    />Install update</)
  const join = render(PatreonUpdates, { patreon: { ...ready, can_apply: false, entitled: false, access: UNLINKED } })
  assert.match(join, /available to Patreon members/)
  assert.match(join, /Members feature/)
  assert.match(join, /Connect Patreon/)
  assert.match(render(PatreonUpdates, { patreon: { ...ready, install_mode: 'docker', blocked_reason: 'Docker limit.' } }),
    /Docker limit\./)
  assert.match(render(PatreonUpdates, { patreon: null, installed: { version: '2026.10.20', install_mode: 'pinokio' } }),
    /Click the Update tab/)
  assert.match(render(PatreonUpdates, { patreon: { status: 'not_configured' } }), /reserved for Patreon members/)
})

test('an install review asks for a Patreon membership, not a purchase', () => {
  const plan = { plan_id: 'x'.repeat(64), requested: 'manga', update_only: false,
    packages: [{ manifest: { id: 'manga', name: 'Manga Studio', version: '1.2.0' }, action: 'install' }],
    purchase_required: ['manga'], patreon_required: ['manga'], paid_packages: ['manga'], patreon_access: UNLINKED }
  const html = render(InstallPlan, { plan, busy: false, onConfirm() {}, onCancel() {}, onAcquire() {}, onRecheck() {} })
  assert.match(html, /Manga Studio is available to Patreon members/)
  assert.match(html, /Connect Patreon/)
  assert.match(html, /Check again/)
  assert.doesNotMatch(html, /Open purchases/)
})

test('a Patreon product is labelled and promises no screenshots', () => {
  const release = { manifest: { id: 'manga', name: 'Manga Studio', version: '1.2.0', description: 'Comics.' },
    price: { kind: 'paid', product: 'patreon_lds', provider: 'patreon' }, changelog: [],
    presentation: { schema_version: 1, images: [] }, compatibility_issues: [] }
  const html = render(PluginCard, { plugin: null, release, storeActions: null })
  assert.match(html, /For Patreon members/)
  assert.doesNotMatch(html, /Screenshots coming soon/)
})

test('the announcement names both tiers and no price', () => {
  const html = render(() => createElement(MemoryRouter, null, createElement(SupportProjectNotice)))
  assert.match(html, /LDS updates now come through Patreon/)
  assert.match(html, /LDS Member and LDS Pro/)
  assert.match(html, /href="\/plugins\?tab=purchases"/)
  assert.doesNotMatch(html, /[$€£]/)
})
