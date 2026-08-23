// Minimal Web Push service worker. Registered on demand from
// lib/webPush.ts's enableWebPush() — not eagerly on every page load — so a
// user who never opens Settings never gets a service worker at all.

self.addEventListener("push", (event) => {
  let payload = { title: "Sentinel", body: "", url: "/alerts" }
  try {
    if (event.data) payload = { ...payload, ...event.data.json() }
  } catch {
    // Not JSON; fall back to the defaults above.
  }

  event.waitUntil(
    self.registration.showNotification(payload.title, {
      body: payload.body,
      data: { url: payload.url },
    }),
  )
})

self.addEventListener("notificationclick", (event) => {
  event.notification.close()
  const url = event.notification.data?.url ?? "/alerts"

  event.waitUntil(
    (async () => {
      const clientsList = await self.clients.matchAll({ type: "window", includeUncontrolled: true })
      const existing = clientsList.find((c) => new URL(c.url).pathname === url)
      if (existing) {
        await existing.focus()
        return
      }
      await self.clients.openWindow(url)
    })(),
  )
})
