/* After an update or a restart request the server re-execs on the same port. The OLD
   process keeps answering /api/health for about a second before it exits (the
   restart waits for its HTTP response to flush), so reloading on the first "ok"
   landed the page on the dying process, stuck on "Network error".

   Resolves true once a NEW process answers (another boot id), or once the server was
   seen down and answers again; false after `timeoutMs`. A server without a boot id
   (older backend) is accepted on its first answer, as before. */
export async function waitForRelaunchedServer({ timeoutMs = 15 * 60 * 1000, intervalMs = 1000, fetchFn = fetch } = {}) {
  const health = () => fetchFn('/api/health', { cache: 'no-store' })
  let before = null
  let sawDown = false
  try {
    const first = await health()
    if (first.ok) before = (await first.json().catch(() => ({}))).boot || null
    else sawDown = true
  } catch { sawDown = true }
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, intervalMs))
    try {
      const res = await health()
      if (!res.ok) { sawDown = true; continue }
      const boot = (await res.json().catch(() => ({}))).boot || null
      if (sawDown || !before || (boot && boot !== before)) return true
    } catch { sawDown = true }   // still restarting — keep waiting
  }
  return false
}
