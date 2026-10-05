import test from 'node:test';
import assert from 'node:assert/strict';

import {
  DOCKER_UPDATE_COMMANDS,
  DOCKER_UPDATE_GUIDE_URL,
  dockerUpdateCommands,
  PINOKIO_UPDATE_STEPS,
  PINOKIO_UPDATE_GUIDE_URL,
  formatMB,
  installMode,
  isDockerInstall,
  isPinokioInstall,
  zipUpdateHeadline,
  progressPercent,
  progressLabel,
  patreonUpdateView,
} from './updateStatus.js';

test('formatMB is compact and empty for unknown sizes', () => {
  assert.equal(formatMB(0), '');
  assert.equal(formatMB(null), '');
  assert.equal(formatMB(42_000_000), '42.0 MB');
  assert.equal(formatMB(150_000_000), '150 MB');   // >=100 MB -> whole number
});

test('installMode distinguishes docker, git, zip, unavailable and unknown', () => {
  assert.equal(installMode({ ok: true, install_mode: 'docker' }), 'docker');
  assert.equal(installMode({ ok: true, is_git: true, update_available: true }), 'git');
  assert.equal(installMode({ ok: true, is_git: false, can_apply: true }), 'zip');
  assert.equal(installMode({ ok: true, is_git: false, can_apply: false }), 'unavailable');
  assert.equal(installMode({ ok: false, reason: 'offline' }), 'unknown');
  assert.equal(installMode(null), 'unknown');
});

test('docker mode is a safety boundary even if other apply flags are present', () => {
  const contradictory = {
    ok: true,
    install_mode: 'docker',
    is_git: true,
    can_apply: true,
    update_available: true,
  };
  assert.equal(isDockerInstall(contradictory), true);
  assert.equal(installMode(contradictory), 'docker');
  assert.equal(isDockerInstall({ install_mode: 'git' }), false);
});

test('a Pinokio install outranks its own git checkout', () => {
  // The payload really does carry is_git/behind (its Update tab pulls those
  // commits); reading it as plain 'git' would put the stranding button back.
  const pinokio = {
    ok: true,
    install_mode: 'pinokio',
    is_git: true,
    behind: 3,
    update_available: true,
  };
  assert.equal(isPinokioInstall(pinokio), true);
  assert.equal(installMode(pinokio), 'pinokio');
  assert.equal(installMode({ ...pinokio, can_apply: true }), 'pinokio');
  assert.equal(isPinokioInstall({ install_mode: 'git' }), false);
  assert.equal(isDockerInstall(pinokio), false);
});

test('pinokio update steps are three clicks, not shell commands', () => {
  assert.deepEqual(PINOKIO_UPDATE_STEPS, [
    'Stop the app in Pinokio',
    'Click the Update tab',
    'Click Start',
  ]);
  assert.equal(Object.isFrozen(PINOKIO_UPDATE_STEPS), true);
  for (const step of PINOKIO_UPDATE_STEPS) {
    assert.doesNotMatch(step, /git |docker |pip /,
      'this install shape never opens a terminal — do not name commands');
  }
  assert.match(PINOKIO_UPDATE_GUIDE_URL, /#option-5--pinokio-one-click-any-os$/);
});

test('docker update instructions preserve the legacy fallback and link to all lanes', () => {
  assert.deepEqual(DOCKER_UPDATE_COMMANDS, [
    'git pull',
    'docker compose -f docker-compose.gpu.yml up -d --build',
  ]);
  assert.equal(Object.isFrozen(DOCKER_UPDATE_COMMANDS), true);
  assert.match(DOCKER_UPDATE_GUIDE_URL, /docs\/guide\/docker.md#updates-and-restarts$/);
});

test('Docker update instructions follow the runtime instead of always rebuilding GPU', () => {
  const commands = ['git pull', 'docker compose up -d --build'];
  assert.deepEqual(dockerUpdateCommands({ instructions: commands }), commands);
  for (const status of [null, {}, { instructions: [] }, { instructions: [null] }]) {
    assert.deepEqual(dockerUpdateCommands(status), DOCKER_UPDATE_COMMANDS);
  }
});

test('zipUpdateHeadline announces the release and its size when known', () => {
  assert.equal(zipUpdateHeadline({ latest: '2026.07.19', zip_size: 42_000_000 }),
    'Update to v2026.07.19 (download ~42.0 MB)');
  assert.equal(zipUpdateHeadline({ latest: '2026.07.19' }), 'Update to v2026.07.19');
  assert.equal(zipUpdateHeadline({}), 'Update available');
});

test('progressPercent clamps and is null without a total', () => {
  assert.equal(progressPercent({ downloaded: 21_000_000, total: 42_000_000 }), 50);
  assert.equal(progressPercent({ downloaded: 99, total: 0 }), null);
  assert.equal(progressPercent({ downloaded: 999, total: 100 }), 100);   // clamped
  assert.equal(progressPercent(null), null);
});

test('progressLabel renders each active phase and defers idle/done', () => {
  assert.match(progressLabel({ phase: 'downloading', downloaded: 21_000_000, total: 42_000_000 }),
    /Downloading… 50% \(21\.0 MB \/ 42\.0 MB\)/);
  assert.match(progressLabel({ phase: 'downloading', downloaded: 5_000_000, total: 0 }),
    /Downloading… 5\.0 MB/);            // unknown total -> no percent
  assert.match(progressLabel({ phase: 'extracting' }), /Extracting/);
  assert.match(progressLabel({ phase: 'installing' }), /Installing/);
  assert.match(progressLabel({ phase: 'restarting' }), /Restarting/);
  assert.equal(progressLabel({ phase: 'done' }), null);
  assert.equal(progressLabel({ phase: 'idle' }), null);
});

test('the Patreon block offers an install only when the server can apply it', () => {
  const ready = { status: 'ready', release: { version: '2026.10.20' }, update_available: true }
  assert.equal(patreonUpdateView(null).kind, 'hidden')
  assert.equal(patreonUpdateView({ status: 'not_configured' }).kind, 'hidden')
  assert.equal(patreonUpdateView({ status: 'unavailable', message: 'offline' }).text, 'offline')
  assert.equal(patreonUpdateView({ ...ready, can_apply: true, entitled: true }).kind, 'install')
  assert.equal(patreonUpdateView({ ...ready, can_apply: false, entitled: false }).kind, 'join')
  // Docker or local changes: never a button, always the server's reason.
  const docker = patreonUpdateView({ ...ready, can_apply: false, entitled: true, blocked_reason: 'Docker limit' })
  assert.deepEqual([docker.kind, docker.reason], ['blocked', 'Docker limit'])
  assert.equal(patreonUpdateView({ ...ready, can_apply: false, entitled: true }).kind, 'blocked')
  // Docker with a release the host updater can install: the command, never a button.
  const host = patreonUpdateView({ ...ready, can_apply: false, entitled: true, host_command: 'update-docker.bat patreon' })
  assert.deepEqual([host.kind, host.command], ['host', 'update-docker.bat patreon'])
  assert.equal(patreonUpdateView({ ...ready, can_apply: false, entitled: false,
    host_command: 'update-docker.bat patreon' }).kind, 'join')
  assert.equal(patreonUpdateView({ ...ready, restart_required: true }).kind, 'restart')
  // A stale verification is a retry, not an invitation to join.
  const stale = patreonUpdateView({ ...ready, can_apply: false, entitled: false,
    access: { status: 'ready', patreon: { linked: true, status: 'verification_required' } } })
  assert.equal(stale.kind, 'blocked')
  assert.match(stale.reason, /could not verify/)
  assert.equal(patreonUpdateView({ status: 'ready', update_available: false, on_channel: true,
    release: { version: '2026.10.20' } }).text, 'Up to date on the Patreon channel (v2026.10.20).')
  assert.equal(patreonUpdateView({ status: 'ready', update_available: false }).kind, 'none')
})
