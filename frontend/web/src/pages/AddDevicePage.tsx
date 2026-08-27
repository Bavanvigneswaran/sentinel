import { useCallback, useEffect, useMemo, useState } from "react"

import { AppLayout } from "@/components/AppLayout"
import { CopyButton, CopyableCommand } from "@/components/CopyButton"
import { EnrollmentCodeCard } from "@/components/EnrollmentCodeCard"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  agentServerOrigin,
  androidInstallSteps,
  archLabelFor,
  buildsFor,
  CODE_PLACEHOLDER,
  enrollmentPlan,
  OS_LABEL,
  shellFor,
  unsignedWarning,
  verifyCommand,
} from "@/lib/agentInstall"
import { ApiError, apiFetch } from "@/lib/api"
import { detectCurrentPlatform, formatBytes, readArchitectureHint } from "@/lib/platform"
import type { AgentBuild, AgentDownloads, BuildOs } from "@/types/downloads"

const ALL_OS: BuildOs[] = ["macos", "linux", "windows", "android"]

/** Where the agent is told to push — see agentServerOrigin(). Resolved once at
 * module scope: neither the origin nor the build mode changes at runtime. */
const agentOrigin = agentServerOrigin(window.location.origin, import.meta.env.DEV)

/**
 * Adding a device: the download and the enrollment code, on one page, in the
 * order they are actually used.
 *
 * These were two pages — `/download` and a "New device" panel on the Devices
 * list — and neither was usable alone. Downloading the agent left you needing
 * a code from somewhere else; minting a code left you needing a binary from
 * somewhere else, and the panel said so by linking to the other page. Two
 * halves of one task that always had to be done together, in two tabs, with
 * the user copying a code between them.
 *
 * Merging them buys something the split could not offer at all: because the
 * code and the chosen build are now in the same component's state, the
 * commands can be rendered **with the real code already in them**. There is
 * nothing to assemble by hand — copy line, paste, next line.
 */
export function AddDevicePage() {
  const [catalog, setCatalog] = useState<AgentDownloads | null>(null)
  const [catalogError, setCatalogError] = useState<string | null>(null)
  const [platform, setPlatform] = useState(() => detectCurrentPlatform())

  // The OS the *instructions* are for, which starts at the detected one and
  // is then the user's choice — the machine being enrolled is very often not
  // the machine the browser is on. That is the common case for a homelab: you
  // are on a laptop, adding a headless box.
  const [chosenOs, setChosenOs] = useState<BuildOs | null>(null)
  const [chosenFilename, setChosenFilename] = useState<string | null>(null)
  const [code, setCode] = useState<string | null>(null)

  useEffect(() => {
    apiFetch<AgentDownloads>("/downloads/agent")
      .then(setCatalog)
      .catch(() => setCatalogError("Could not load the list of agent builds."))
  }, [])

  useEffect(() => {
    let cancelled = false
    readArchitectureHint().then((hint) => {
      if (!cancelled && hint) setPlatform(detectCurrentPlatform(hint))
    })
    return () => {
      cancelled = true
    }
  }, [])

  // "ios" is a DetectedOs and not a BuildOs — it deliberately falls through to
  // null rather than being cast, so the picker has no selection and the page
  // says there is no build instead of rendering a broken card.
  const detectedOs: BuildOs | null =
    platform.os && (ALL_OS as string[]).includes(platform.os) ? (platform.os as BuildOs) : null
  const os = chosenOs ?? detectedOs

  const builds = useMemo(
    () =>
      catalog && os
        ? buildsFor(
            catalog.builds,
            os,
            // Only the *detected* OS can use the detected architecture. Asking
            // for Linux from a Mac must not filter Linux builds by the Mac's
            // arch.
            os === detectedOs ? platform.arch : null,
            os === detectedOs ? platform.archCertain : false,
          )
        : [],
    [catalog, os, detectedOs, platform.arch, platform.archCertain],
  )

  const build =
    builds.find((b) => b.filename === chosenFilename) ?? (builds.length > 0 ? builds[0] : null)

  const onAndroid = os === "android"

  return (
    <AppLayout active="add-device">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Add a device</h1>
        <p className="text-sm text-muted-foreground">
          Put a machine under monitoring: download the agent for it, mint a one-time code, and
          run three commands. You need none of this to use the console itself — this page is
          only for the machines you want it to watch.
        </p>
      </div>

      {catalogError && (
        <Card>
          <CardContent className="pt-6 text-sm text-destructive">{catalogError}</CardContent>
        </Card>
      )}

      {catalog === null && !catalogError && (
        <p className="text-sm text-muted-foreground">Loading builds…</p>
      )}

      {catalog && (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">What are you adding?</CardTitle>
              <CardDescription>
                The machine you want to monitor — which is often not the one you are reading
                this on.
                {detectedOs && (
                  <>
                    {" "}
                    This browser looks like{" "}
                    <span className="font-medium text-foreground">{platform.label}</span>.
                  </>
                )}
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-2">
              {ALL_OS.map((candidate) => (
                <Button
                  key={candidate}
                  size="sm"
                  variant={os === candidate ? "default" : "outline"}
                  onClick={() => {
                    setChosenOs(candidate)
                    setChosenFilename(null)
                  }}
                >
                  {OS_LABEL[candidate]}
                  {candidate === detectedOs && (
                    <span className="text-xs font-normal opacity-70">· this machine</span>
                  )}
                </Button>
              ))}
            </CardContent>
          </Card>

          {os === null && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Pick a platform above</CardTitle>
                <CardDescription>
                  {platform.os === "ios"
                    ? "There is no agent for iOS: it does not let an app sample the device's own system metrics in the background, so there is nothing honest to ship. You can still sign in here from Safari to watch machines enrolled elsewhere."
                    : "This browser did not identify itself as a platform we publish an agent for."}
                </CardDescription>
              </CardHeader>
            </Card>
          )}

          {os !== null && builds.length === 0 && (
            <NoBuild os={os} catalog={catalog} code={code} onCode={setCode} />
          )}

          {os !== null && builds.length > 0 && build !== null && (
            <>
              <Step
                n={1}
                title={onAndroid ? "Get the app" : `Download the agent for ${OS_LABEL[os]}`}
              >
                {builds.length > 1 && (
                  <div className="flex flex-col gap-2">
                    <span className="text-xs font-medium text-muted-foreground">
                      Which processor does that machine have?
                    </span>
                    <div className="flex flex-wrap gap-2">
                      {builds.map((candidate) => (
                        <Button
                          key={candidate.filename}
                          size="sm"
                          variant={
                            candidate.filename === build.filename ? "default" : "outline"
                          }
                          onClick={() => setChosenFilename(candidate.filename)}
                        >
                          {archLabelFor(candidate)}
                        </Button>
                      ))}
                    </div>
                    {os === "macos" && (
                      <p className="text-xs text-muted-foreground">
                        A browser cannot tell you this — Safari and Chrome both report an Apple
                        Silicon Mac as “Intel”, permanently, for compatibility. Apple Silicon is
                        anything from an M1 onwards; <code className="font-mono">uname -m</code>{" "}
                        says <code className="font-mono">arm64</code> for it and{" "}
                        <code className="font-mono">x86_64</code> for Intel.
                      </p>
                    )}
                  </div>
                )}
                <DownloadRow build={build} />
                <Verify build={build} hostOs={detectedOs} />
              </Step>

              {onAndroid ? (
                <Step n={2} title="Install it and sign in">
                  <ol className="flex list-decimal flex-col gap-2 pl-5 text-sm text-muted-foreground">
                    {androidInstallSteps().map((step) => (
                      <li key={step}>{step}</li>
                    ))}
                  </ol>
                  <p className="text-xs text-muted-foreground">
                    There is deliberately no enrollment code on this page for Android. The
                    signed-in app mints its own, short-lived, internally, and hands it straight
                    to its own collector — so there is nothing to copy and nothing to type.
                  </p>
                </Step>
              ) : (
                <>
                  <Step n={2} title="Mint an enrollment code">
                    <EnrollmentCodeCard onCode={setCode} />
                  </Step>

                  <Step
                    n={3}
                    title={`Run these on that machine, in ${shellFor(build)}`}
                  >
                    {code === null && (
                      <p className="text-sm text-muted-foreground">
                        These are the real commands for this build. Mint a code above and it is
                        substituted into the enrol line — until then it reads{" "}
                        <code className="font-mono text-xs">{CODE_PLACEHOLDER}</code>.
                      </p>
                    )}
                    <div className="flex flex-col gap-4">
                      {enrollmentPlan(build, code, agentOrigin).map((step) => (
                        <div key={step.command} className="flex flex-col gap-1">
                          <CopyableCommand command={step.command} />
                          <p className="text-xs text-muted-foreground">{step.note}</p>
                        </div>
                      ))}
                    </div>
                    <UnsignedNote build={build} />
                  </Step>
                </>
              )}
            </>
          )}

          <p className="text-xs text-muted-foreground">
            Builds published{" "}
            {catalog.generated_at
              ? new Date(catalog.generated_at).toLocaleString()
              : "at an unrecorded time"}
            . No build published for:{" "}
            {ALL_OS.filter((candidate) => !catalog.builds.some((b) => b.os === candidate))
              .map((candidate) => OS_LABEL[candidate])
              .join(", ") || "none"}
            .
          </p>
        </>
      )}
    </AppLayout>
  )
}

/** One numbered step. The numbering is the point of the merge — the two old
 *  pages each held a subset of a sequence and neither could show its order. */
function Step({
  n,
  title,
  children,
}: {
  n: number
  title: string
  children: React.ReactNode
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-3 text-base">
          <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">
            {n}
          </span>
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">{children}</CardContent>
    </Card>
  )
}

function DownloadRow({ build }: { build: AgentBuild }) {
  const [ticket, setTicket] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // An absolute URL means a release host is serving the file, no auth of ours
  // involved. A relative one is this API and needs a credential the download
  // manager can carry in place of an Authorization header — see below.
  const external = /^https?:\/\//i.test(build.download_url)

  const mintTicket = useCallback(() => {
    if (external) return
    apiFetch<{ ticket: string; expires_in: number }>(
      `/downloads/agent/${encodeURIComponent(build.filename)}/ticket`,
      { method: "POST" },
    )
      .then((res) => {
        setTicket(res.ticket)
        setError(null)
      })
      .catch((err) => {
        setTicket(null)
        setError(
          err instanceof ApiError && err.status === 404
            ? "This build is no longer on the server — it may have been rebuilt since this page loaded. Refresh and try again."
            : "Could not reach the server to prepare the download. Check your connection and try again.",
        )
      })
      .finally(() => setLoading(false))
  }, [external, build.filename])

  // Minted ahead of the tap, not on it, so a real user click lands on a
  // ready `<a href>` rather than one built and clicked by JS after an await.
  useEffect(() => {
    mintTicket()
    // A ticket is only valid for a few minutes (download_tickets.py). If this
    // tab sat in the background longer than that, refresh it the moment it's
    // foregrounded again rather than let a tap fail on a stale one.
    const onVisible = () => {
      if (document.visibilityState === "visible") mintTicket()
    }
    document.addEventListener("visibilitychange", onVisible)
    return () => document.removeEventListener("visibilitychange", onVisible)
  }, [mintTicket])

  // build.download_url is deliberately un-prefixed (it's also what apiFetch's
  // callers pass in, which adds /api itself — see lib/api.ts). A real link
  // click is a browser navigation, not a fetch(), so nothing adds that prefix
  // here automatically: without it, the console's own routing middleware
  // reads this as a client-side route it owns and serves index.html instead
  // of ever reaching the download route — the SPA shell, not the APK, is
  // exactly what was silently coming back as "app-release.apk.html".
  const href = external
    ? build.download_url
    : ticket
      ? `/api${build.download_url}?ticket=${encodeURIComponent(ticket)}`
      : null

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3">
        {href ? (
          <Button asChild>
            <a href={href} download={build.filename}>
              Download {build.filename}
            </a>
          </Button>
        ) : (
          <Button
            onClick={() => {
              setLoading(true)
              mintTicket()
            }}
            disabled={loading}
          >
            {loading ? "Preparing…" : "Retry"}
          </Button>
        )}
        <span className="text-xs text-muted-foreground">
          {formatBytes(build.size_bytes)} · v{build.version} ·{" "}
          {build.signed ? "signed" : "unsigned"}
        </span>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  )
}

function Verify({ build, hostOs }: { build: AgentBuild; hostOs: BuildOs | null }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs font-medium text-muted-foreground">
        {build.os === "android"
          ? "Optional: check it on this computer before transferring it"
          : "Optional: check it matches what this server published"}
      </span>
      <CopyableCommand command={verifyCommand(build, hostOs)} />
      <div className="flex items-center gap-2">
        <code className="overflow-x-auto font-mono text-[11px] text-muted-foreground">
          {build.sha256}
        </code>
        <CopyButton value={build.sha256} label="Copy hash" variant="ghost" />
      </div>
    </div>
  )
}

/** What the OS will say the first time, quoted before the click. */
function UnsignedNote({ build }: { build: AgentBuild }) {
  const warning = unsignedWarning(build)
  if (!warning) return null

  return (
    <div className="flex flex-col gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
      <span className="text-sm font-medium">
        {OS_LABEL[build.os]} will interrupt this the first time
      </span>
      <p className="text-xs text-muted-foreground">What you will see:</p>
      {/* Verbatim, so the user can match it against the dialog in front of
          them rather than wondering whether they downloaded malware. Not
          wrapped in quotes — the macOS text contains its own. */}
      <p className="border-l-2 border-amber-500/40 pl-3 text-sm italic text-muted-foreground">
        {warning.dialog}
      </p>
      <ul className="flex list-disc flex-col gap-1 pl-5 text-sm text-muted-foreground">
        {warning.steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ul>
      <p className="text-xs text-muted-foreground">
        No code-signing certificates have been bought for this project, so every build is
        unsigned. The checksum is what stands in for a signature.
      </p>
    </div>
  )
}

/**
 * No build for this OS. Deliberately not a disabled button or a link that
 * 404s: say what is missing and give the command that works instead — the
 * same posture the rest of the product takes to an unconfigured integration.
 */
function NoBuild({
  os,
  catalog,
  code,
  onCode,
}: {
  os: BuildOs
  catalog: AgentDownloads
  code: string | null
  onCode: (code: string | null) => void
}) {
  if (os === "android") {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">No Android build has been published here</CardTitle>
          <CardDescription>
            {catalog.unavailable_reason ??
              "The APK is built from frontend/mobile — see docs/PACKAGING.md."}
          </CardDescription>
        </CardHeader>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          No {OS_LABEL[os]} build has been published here
        </CardTitle>
        <CardDescription>
          {catalog.unavailable_reason ??
            `This server has agent builds, but none for ${OS_LABEL[os]}.`}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">
          PyInstaller cannot cross-compile, so each platform's binary has to be produced on that
          platform. Until one is published for {OS_LABEL[os]}, run the agent from source — same
          code, needs only Python 3.11+.
        </p>
        <CopyableCommand command={catalog.source_build_command} />
        <p className="text-sm text-muted-foreground">Or skip packaging and run it directly:</p>
        <CopyableCommand
          command={`cd agent && pip install -e . && sentinel-agent enroll --code ${
            code ?? CODE_PLACEHOLDER
          }`}
        />
        <EnrollmentCodeCard onCode={onCode} />
      </CardContent>
    </Card>
  )
}
