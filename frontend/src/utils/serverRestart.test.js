import { test } from 'node:test'
import assert from 'node:assert/strict'
import { waitForRelaunchedServer } from './serverRestart.js'

// Each entry is one /api/health answer: a boot id, 'down' (connection refused) or 'legacy'.
function scripted(answers) {
  let i = 0
  const fetchFn = async () => {
    const answer = answers[Math.min(i, answers.length - 1)]
    i += 1
    if (answer === 'down') throw new TypeError('Failed to fetch')
    return { ok: true, json: async () => (answer === 'legacy' ? { ok: true } : { ok: true, boot: answer }) }
  }
  return { fetchFn, calls: () => i }
}

test('the old process answering before it exits never counts as the relaunch', async () => {
  const s = scripted(['old', 'old', 'old', 'new'])
  assert.equal(await waitForRelaunchedServer({ fetchFn: s.fetchFn, intervalMs: 1, timeoutMs: 1000 }), true)
  assert.equal(s.calls(), 4)
})

test('down then back counts as the relaunch', async () => {
  const s = scripted(['old', 'down', 'down', 'old'])
  assert.equal(await waitForRelaunchedServer({ fetchFn: s.fetchFn, intervalMs: 1, timeoutMs: 1000 }), true)
})

test('a server that never restarts times out instead of reloading', async () => {
  const s = scripted(['old'])
  assert.equal(await waitForRelaunchedServer({ fetchFn: s.fetchFn, intervalMs: 1, timeoutMs: 30 }), false)
})

test('an older backend without a boot id keeps the previous behavior', async () => {
  const s = scripted(['legacy', 'legacy'])
  assert.equal(await waitForRelaunchedServer({ fetchFn: s.fetchFn, intervalMs: 1, timeoutMs: 1000 }), true)
  assert.equal(s.calls(), 2)
})
