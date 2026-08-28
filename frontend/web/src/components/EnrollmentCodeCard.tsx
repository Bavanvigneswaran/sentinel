import { useEffect, useState } from "react"

import { CopyButton } from "@/components/CopyButton"
import { Button } from "@/components/ui/button"
import { ApiError, apiFetch } from "@/lib/api"
import type { EnrollmentCode } from "@/types/api"

/** Re-render often enough that the countdown is never stale by more than a
 * second — a code the page still shows as valid but the server has already
 * expired is exactly the kind of small lie this project avoids. */
const TICK_MS = 1000

function remaining(expiresAt: string, now: number): string {
  const seconds = Math.floor((new Date(expiresAt).getTime() - now) / 1000)
  if (seconds <= 0) return "expired"
  const minutes = Math.floor(seconds / 60)
  return minutes > 0 ? `expires in ${minutes}m ${seconds % 60}s` : `expires in ${seconds}s`
}

/**
 * Mints a one-time enrollment code, shows it once, and hands it to the page.
 *
 * There is deliberately no device-name field: a device row is created by the
 * *agent* at enrollment time from the machine's own hostname, so asking for a
 * name here would either be ignored or become a second source of truth. The
 * user can rename afterwards.
 *
 * `onCode` is the reason this is a component and not a page of its own any
 * more (it was `NewDevicePanel`): the code has to reach the commands rendered
 * below it, so the user never has to transcribe it into anything.
 */
export function EnrollmentCodeCard({ onCode }: { onCode: (code: string | null) => void }) {
  const [issued, setIssued] = useState<EnrollmentCode | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [minting, setMinting] = useState(false)
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
      setNow(Date.now())
      onCode(code.code)
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

  const expired = issued !== null && new Date(issued.expires_at).getTime() <= now

  return (
    <div className="flex flex-col gap-3">
      {error && <p className="text-sm text-destructive">{error}</p>}

      {issued === null ? (
        <>
          <p className="text-sm text-muted-foreground">
            Enrolling is a one-time exchange: this mints a short-lived, single-use code, and the
            agent redeems it for its own revocable token. Your password never reaches the agent.
          </p>
          <Button className="w-fit" onClick={() => void mint()} disabled={minting}>
            {minting ? "Minting…" : "Generate enrollment code"}
          </Button>
        </>
      ) : (
        <>
          <span className="text-xs font-medium text-muted-foreground">
            Shown once — only its hash is stored, so a code you lose has to be replaced rather
            than looked up.
          </span>
          <div className="flex flex-wrap items-center gap-3">
            <code className="select-all rounded-md border border-border bg-muted px-3 py-2 font-mono text-lg tracking-widest">
              {issued.code}
            </code>
            <CopyButton value={issued.code} disabled={expired} />
            <span className={expired ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>
              {remaining(issued.expires_at, now)}
            </span>
          </div>
          <p className="text-xs text-muted-foreground">
            It is already filled into the commands below — you do not need to copy it separately
            unless you are typing them somewhere else.
          </p>
          <Button
            className="w-fit"
            size="sm"
            variant="outline"
            onClick={() => void mint()}
            disabled={minting}
          >
            {minting ? "Minting…" : "Generate another"}
          </Button>
        </>
      )}
    </div>
  )
}
