import { useEffect, useState } from "react"
import { Link } from "react-router"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { ApiError, apiFetch } from "@/lib/api"
import type { EnrollmentCode } from "@/types/api"

/** Re-render often enough that the countdown below is never stale by more
 * than a second — a code the page still shows as valid but the server has
 * already expired is exactly the kind of small lie this project avoids. */
const TICK_MS = 1000

function remaining(expiresAt: string, now: number): string {
  const seconds = Math.floor((new Date(expiresAt).getTime() - now) / 1000)
  if (seconds <= 0) return "expired"
  const minutes = Math.floor(seconds / 60)
  return minutes > 0 ? `expires in ${minutes}m ${seconds % 60}s` : `expires in ${seconds}s`
}

/**
 * Mints a one-time enrollment code and shows it once.
 *
 * There is deliberately no device-name field: a device row is created by the
 * *agent* at enrollment time from the machine's own hostname, so asking for a
 * name here would either be ignored or become a second source of truth. The
 * user can rename afterwards.
 */
export function NewDevicePanel({ onClose }: { onClose: () => void }) {
  const [issued, setIssued] = useState<EnrollmentCode | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [minting, setMinting] = useState(false)
  const [copied, setCopied] = useState(false)
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!issued) return
    const interval = setInterval(() => setNow(Date.now()), TICK_MS)
    return () => clearInterval(interval)
  }, [issued])

  const mint = async () => {
    setMinting(true)
    setError(null)
    try {
      const code = await apiFetch<EnrollmentCode>("/enrollment-codes", {
        method: "POST",
        body: {},
      })
      setIssued(code)
      setCopied(false)
      setNow(Date.now())
    } catch (err) {
      // The endpoint is rate limited per IP (20/hour). Say which wall was hit
      // rather than a generic failure, or the user just retries into it.
      setError(
        err instanceof ApiError && err.status === 429
          ? "Too many codes minted from this network in the last hour. Try again later."
          : "Could not mint an enrollment code.",
      )
    } finally {
      setMinting(false)
    }
  }

  const copy = () => {
    if (!issued) return
    // navigator.clipboard is unavailable over plain http:// on a non-loopback
    // origin, which is exactly how this console is reached over the LAN. Fall
    // back to leaving the code on screen to be typed rather than pretending.
    navigator.clipboard
      ?.writeText(issued.code)
      .then(() => setCopied(true))
      .catch(() => setError("Couldn't copy — select the code above and copy it by hand."))
  }

  const expired = issued !== null && new Date(issued.expires_at).getTime() <= now

  return (
    <Card>
      <CardHeader>
        <CardTitle>Add a device</CardTitle>
        <CardDescription>
          Enrolling is a one-time exchange: this page mints a short-lived code, and the agent on
          the machine you want to monitor redeems it for its own revocable token. Your password
          never reaches the agent.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {error && <p className="text-sm text-destructive">{error}</p>}

        {issued === null ? (
          <div className="flex gap-2">
            <Button size="sm" onClick={mint} disabled={minting}>
              {minting ? "Minting…" : "Generate enrollment code"}
            </Button>
            <Button size="sm" variant="outline" onClick={onClose}>
              Cancel
            </Button>
          </div>
        ) : (
          <>
            <div className="flex flex-col gap-2">
              <span className="text-xs font-medium text-muted-foreground">
                Copy this now — it is shown once and never stored in readable form.
              </span>
              <div className="flex flex-wrap items-center gap-3">
                <code className="select-all rounded-md border border-border bg-muted px-3 py-2 font-mono text-lg tracking-widest">
                  {issued.code}
                </code>
                <Button size="sm" variant="outline" onClick={copy} disabled={expired}>
                  {copied ? "Copied" : "Copy"}
                </Button>
                <span
                  className={
                    expired ? "text-xs text-destructive" : "text-xs text-muted-foreground"
                  }
                >
                  {remaining(issued.expires_at, now)}
                </span>
              </div>
            </div>

            <div className="flex flex-col gap-1 text-sm text-muted-foreground">
              <span className="text-xs font-medium">On the machine you want to monitor:</span>
              <span>
                <strong className="font-medium text-foreground">Desktop</strong> — install the
                agent from the{" "}
                <Link to="/download" className="underline">
                  Install an agent
                </Link>{" "}
                page, then run its <code className="font-mono text-xs">enroll</code> command with
                the code above.
              </span>
              <span>
                <strong className="font-medium text-foreground">Android</strong> — you do not need
                this code. The app enrols itself from its own Monitor this phone screen.
              </span>
            </div>

            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={mint} disabled={minting}>
                {minting ? "Minting…" : "Generate another"}
              </Button>
              <Button size="sm" variant="outline" onClick={onClose}>
                Done
              </Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
