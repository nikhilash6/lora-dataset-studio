import test from 'node:test';
import assert from 'node:assert/strict';

import {
  FREE_TRIAL_TEXT, PATREON_ACCESS_SUMMARY, isPatreonRelease, membershipLabel, patreonActions,
  safeAuthorizeUrl, safeJoinUrl, safeRequestUrl, tierLabel,
} from './patreonModel.js';

const JOIN = 'https://www.patreon.com/c/example';
const linked = (over = {}) => ({ status: 'ready', patreon: { linked: true, status: 'active', tier: 'member',
  valid_until: null, join_url: JOIN, request_url: null, ...over } });

test('only the pinned Patreon authorization page becomes a link', () => {
  const good = 'https://www.patreon.com/oauth2/authorize?response_type=code&state=abc';
  assert.equal(safeAuthorizeUrl(good), good);
  for (const bad of ['https://www.patreon.com/oauth2/authorize', 'http://www.patreon.com/oauth2/authorize?x=1',
    'https://patreon.com/oauth2/authorize?x=1', 'https://www.patreon.com.example.test/oauth2/authorize?x=1',
    'https://www.patreon.com:444/oauth2/authorize?x=1', 'https://a@www.patreon.com/oauth2/authorize?x=1',
    'https://www.patreon.com/oauth2/authorize?x=1#frag', 'javascript:alert(1)', null]) {
    assert.equal(safeAuthorizeUrl(bad), '', String(bad));
  }
  assert.equal(safeJoinUrl(JOIN), JOIN);
  assert.equal(safeJoinUrl('https://evil.example.test/join'), '');
  assert.equal(safeRequestUrl('https://requests.example.test/form'), 'https://requests.example.test/form');
  assert.equal(safeRequestUrl('http://requests.example.test/form'), '');
});

test('actions follow the membership: connect, join, disconnect, custom requests', () => {
  const none = { connect: false, reconnect: false, join: false, disconnect: false, request: false, confirm: false };
  assert.deepEqual(patreonActions({ status: 'not_connected', patreon: { linked: false, status: 'inactive',
    tier: null, join_url: JOIN, request_url: null } }), { ...none, connect: true, join: true });
  assert.deepEqual(patreonActions(linked()), { ...none, disconnect: true });
  assert.deepEqual(patreonActions(linked({ tier: 'pro', request_url: 'https://requests.example.test/form' })),
    { ...none, disconnect: true, request: true });
  // Patreon has no lookup by user: a linked non-member may join, then connect again.
  assert.deepEqual(patreonActions(linked({ status: 'inactive', tier: null })),
    { ...none, connect: true, reconnect: true, join: true, disconnect: true });
  for (const status of ['not_configured', 'unavailable', 'not_permitted']) {
    assert.deepEqual(patreonActions({ status, patreon: null }), none);
  }
});

test('a pending approval asks for the code, and a free trial says when access starts', () => {
  const pending = { status: 'not_connected', patreon: { linked: false, status: 'inactive', tier: null,
    join_url: JOIN, request_url: null, pending: true, reason: null } };
  assert.equal(patreonActions(pending).confirm, true);
  assert.match(membershipLabel(pending), /Enter the code shown on the Patreon page/);
  assert.equal(patreonActions(linked()).confirm, false);
  const trial = linked({ status: 'inactive', tier: null, reason: 'free_trial' });
  assert.equal(membershipLabel(trial), FREE_TRIAL_TEXT);
  assert.match(FREE_TRIAL_TEXT, /Access starts with your first payment on Patreon/);
  // An operator who allows free trials sees them as active: no warning then.
  assert.match(membershipLabel(linked({ reason: 'free_trial' })), /^Active Patreon membership/);
});

test('labels name the LDS Member tier and never carry a price', () => {
  assert.equal(tierLabel('member'), 'LDS Member');
  assert.equal(tierLabel('pro'), '');  // No Pro tier is offered: an operator-configured one stays unnamed.
  assert.equal(membershipLabel(linked({ tier: 'pro' })), 'Active Patreon membership');
  assert.match(membershipLabel({ status: 'not_configured' }), /not available/);
  assert.doesNotMatch(PATREON_ACCESS_SUMMARY, /[$€£]|\d+\s*(per|a)\s*month/i);
  assert.equal(isPatreonRelease({ price: { kind: 'paid', provider: 'patreon' } }), true);
  assert.equal(isPatreonRelease({ price: { kind: 'paid' } }), false);
});
