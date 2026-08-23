import { useEffect, useMemo, useState } from "react"
import { Link } from "react-router"

import { AppLayout } from "@/components/AppLayout"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  androidInstallSteps,
  ARCH_LABEL,
  archLabelFor,
  buildsFor,
  installSteps,
  isCliInstall,
  isDesktopAgent,
  OS_LABEL,
  SERVICE_MECHANISM,
  unsignedWarning,
  verifyCommand,
} from "@/lib/agentInstall"
import { apiDownload, apiFetch } from "@/lib/api"
import { triggerDownload } from "@/lib/download"
import {
  type DetectedPlatform,
  detectCurrentPlatform,
  formatBytes,
  readArchitectureHint,
} from "@/lib/platform"
import type { AgentBuild, AgentDownloads, BuildOs } from "@/types/downloads"

const ALL_OS: BuildOs[] = ["macos", "linux", "windows", "android"]

function Code({ children }: { children: string }) {
  return (
    <code className="block overflow-x-auto rounded bg-muted px-3 py-2 font-mono text-xs">
      {children}
    </code>
  )
}

/**
 * One build: what it is, whether it is signed, and how to check it is the file
 * the server published.
 */
function BuildCard({ build }: { build: AgentBuild }) {
  const [downloading, setDownloading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const warning = unsignedWarning(build)

  // An absolute URL means a release host is serving the file, and fetching it
  // for a blob would be a cross-origin request the API's auth header has no
  // business on. A relative one is this API, behind the bearer token, so it
  // has to go through apiDownload like a report export does.
  const external = /^https?:\/\//i.test(build.download_url)

  const handleDownload = async () => {
    setDownloading(true)
    setError(null)
    try {
      const { blob, filename } = await apiDownload(build.download_url)
      triggerDownload(blob, filename || build.filename)
    } catch {
      setError("The download failed. The build may have been removed from this server.")
    } finally {
      setDownloading(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          {OS_LABEL[build.os]}
          <span className="text-sm font-normal text-muted-foreground">
            {archLabelFor(build)}
          </span>
          <span className="rounded bg-muted px-2 py-0.5 font-mono text-xs font-normal">
            v{build.version}
          </span>
        </CardTitle>
        <CardDescription>
          {formatBytes(build.size_bytes)} ·{" "}
          {build.signed ? "signed" : "unsigned — see below"}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {external ? (
          <Button asChild className="w-fit">
            <a href={build.download_url} download={build.filename}>
              Download {build.filename}
            </a>
          </Button>
        ) : (
          <Button className="w-fit" onClick={handleDownload} disabled={downloading}>
            {downloading ? "Downloading…" : `Download ${build.filename}`}
          </Button>
        )}
        {error && <p className="text-sm text-destructive">{error}</p>}

        <div className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted-foreground">
            {build.os === "android"
              ? "Verify it on this computer before you transfer it to your phone"
              : "Verify it before you run it"}
          </span>
          <Code>{verifyCommand(build)}</Code>
          <code className="overflow-x-auto font-mono text-[11px] text-muted-foreground">
            {build.sha256}
          </code>
        </div>

        {warning && (
          <div className="flex flex-col gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
            <span className="text-sm font-medium">
              {OS_LABEL[build.os]} will block this the first time
            </span>
            {/* Verbatim, so the user can match it against the dialog in front
                of them rather than wondering whether they downloaded malware.
                Not wrapped in quotes — the macOS text contains its own. */}
            <p className="text-xs text-muted-foreground">What you will see:</p>
            <p className="border-l-2 border-amber-500/40 pl-3 text-sm italic text-muted-foreground">
              {warning.dialog}
            </p>
            <ul className="flex list-disc flex-col gap-1 pl-5 text-sm text-muted-foreground">
              {warning.steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ul>
            <p className="text-xs text-muted-foreground">
              No code-signing certificates have been bought for this project yet, so every
              build is unsigned. The checksum above is what stands in for a signature.
            </p>
          </div>
        )}

        <div className="flex flex-col gap-1">
          {isCliInstall(build) ? (
            <>
              <span className="text-xs font-medium text-muted-foreground">
                Then enrol it — mint a code on the{" "}
                <Link to="/devices" className="underline">
                  Devices
                </Link>{" "}
                page first
              </span>
              {installSteps(build).map((step) => (
                <Code key={step}>{step}</Code>
              ))}
            </>
          ) : (
            // Android has no CLI to enrol from — the app mints its own code
            // internally and there is nothing to type. Plain steps, not shell
            // commands: rendering `./sentinel.apk enroll --code …` here would
            // be a command that cannot be run anywhere on a phone.
            <ol className="flex list-decimal flex-col gap-1 pl-5 text-sm text-muted-foreground">
              {androidInstallSteps().map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          )}
          <span className="text-xs text-muted-foreground">
            {isCliInstall(build)
              ? `install-service registers ${SERVICE_MECHANISM[build.os]}.`
              : SERVICE_MECHANISM[build.os]}
          </span>
        </div>
      </CardContent>
    </Card>
  )
}

/**
 * What to say when there is no build for this visitor's OS.
 *
 * Deliberately not a disabled button or a link that 404s: the same posture the
 * rest of the product takes to an unconfigured integration — say what is
 * missing and what to do instead.
 */
function NoBuild({
  platform,
  catalog,
}: {
  platform: DetectedPlatform
  catalog: AgentDownloads
}) {
  if (platform.os === "ios") {
    return (
      <Card>
        <CardHeader>
          <CardTitle>There is no agent for iOS</CardTitle>
          <CardDescription>
            iOS does not let an app sample the device's own system metrics in the background,
            so there is nothing honest to ship. You can still sign in here from Safari to
            watch machines you have enrolled elsewhere; the Android app is a monitored device
            as well as a viewer.
          </CardDescription>
        </CardHeader>
      </Card>
    )
  }

  const name = platform.os ? OS_LABEL[platform.os] : platform.label

  return (
    <Card>
      <CardHeader>
        <CardTitle>No {name} build has been published here</CardTitle>
        <CardDescription>
          {catalog.unavailable_reason ??
            `This server has agent builds, but none for ${name}.`}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">
          PyInstaller cannot cross-compile, so each platform's binary has to be produced on
          that platform. Until a build is published for {name}, run the agent from source —
          it is the same code, and needs only Python 3.11+.
        </p>
        <Code>{catalog.source_build_command}</Code>
        <p className="text-sm text-muted-foreground">
          Or skip packaging entirely and run it directly:
        </p>
        <Code>{`cd agent && pip install -e . && sentinel-agent enroll --code <YOUR-CODE>`}</Code>
      </CardContent>
    </Card>
  )
}

/**
 * The Android app, kept apart from the three agent binaries.
 *
 * Not a styling preference. Everything the "Desktop agents" heading says is
 * false of this build: it is not headless, it is not installed onto a machine
 * you then watch from somewhere else, and — the part that actually misled —
 * a phone does not need an agent installed onto it at all. The collector is
 * already inside the app, off by default, one tap away. Presented as a fourth
 * row under "Install an agent" it read as a fourth agent to install.
 */
function AndroidSection({ builds }: { builds: AgentBuild[] }) {
  return (
    <div className="flex flex-col gap-3">
      <div>
        <h2 className="text-base font-semibold">The Android app</h2>
        <p className="text-sm text-muted-foreground">
          Not an agent — the console itself, for a phone. Sign in and it shows the same fleet,
          alerts, incidents, forecasts and reports you are looking at now. It can also report
          the phone's own metrics, but that is a switch inside the app rather than anything to
          install: there is no separate Android agent, and nothing on this page to paste into
          one.
        </p>
      </div>
      {builds.map((build) => (
        <BuildCard key={build.filename} build={build} />
      ))}
    </div>
  )
}

/**
 * The download page.
 *
 * Detects the visitor's OS and leads with the build they need — except that a
 * Mac's architecture is genuinely undetectable from a browser (see
 * lib/platform.ts), so both Mac builds are offered with an explanation rather
 * than one of them guessed at.
 */
export function DownloadPage() {
  const [catalog, setCatalog] = useState<AgentDownloads | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [platform, setPlatform] = useState<DetectedPlatform>(() => detectCurrentPlatform())

  useEffect(() => {
    apiFetch<AgentDownloads>("/downloads/agent")
      .then(setCatalog)
      .catch(() => setError("Could not load the list of agent builds."))
  }, [])

  useEffect(() => {
    // Chromium can tell us the architecture the user agent refuses to. It
    // resolves to undefined everywhere else, which leaves the initial
    // detection — and its archCertain: false — exactly as it was.
    let cancelled = false
    readArchitectureHint().then((hint) => {
      if (!cancelled && hint) setPlatform(detectCurrentPlatform(hint))
    })
    return () => {
      cancelled = true
    }
  }, [])

  // platform.os may be "ios", which is not a BuildOs — buildsFor takes a plain
  // string for exactly that reason and returns nothing, so NoBuild renders the
  // "there is no agent for iOS" card. Casting it to BuildOs here would be a
  // lie that happens to work.
  const mine = useMemo(
    () =>
      catalog ? buildsFor(catalog.builds, platform.os, platform.arch, platform.archCertain) : [],
    [catalog, platform],
  )

  const others = useMemo(
    () => (catalog ? catalog.builds.filter((b) => !mine.includes(b)) : []),
    [catalog, mine],
  )

  // Two sections, not one list with the phone at the bottom of it. See
  // isDesktopAgent() for why the distinction is real rather than cosmetic.
  const myDesktop = useMemo(() => mine.filter(isDesktopAgent), [mine])
  const otherDesktop = useMemo(() => others.filter(isDesktopAgent), [others])
  const androidBuilds = useMemo(
    () => (catalog ? catalog.builds.filter((b) => !isDesktopAgent(b)) : []),
    [catalog],
  )
  const onAndroid = platform.os === "android"

  const ambiguousArch = mine.length > 1 && !platform.archCertain

  return (
    <AppLayout active="download">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Downloads</h1>
        <p className="text-sm text-muted-foreground">
          Two different things live here: a headless <strong className="font-medium text-foreground">agent</strong>{" "}
          for each desktop OS, and the{" "}
          <strong className="font-medium text-foreground">Android app</strong>, which is a
          console in its own right. You need neither to use this web console — it already runs
          in any modern browser, on any OS.
        </p>
      </div>

      {error && (
        <Card>
          <CardContent className="pt-6 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      {catalog === null && !error && (
        <p className="text-sm text-muted-foreground">Loading builds…</p>
      )}

      {catalog && (
        <>
          <p className="text-sm text-muted-foreground">
            You appear to be on <span className="font-medium text-foreground">{platform.label}</span>
            {platform.archCertain && platform.arch ? ` (${ARCH_LABEL[platform.arch]})` : ""}.
          </p>

          {ambiguousArch && (
            <Card>
              <CardContent className="pt-6 text-sm text-muted-foreground">
                Your browser will not say which processor this machine has — Safari and Chrome
                both report an Apple Silicon Mac as “Intel”, permanently, for compatibility.
                Rather than guess and hand you a binary that cannot run: Apple Silicon is
                anything from an M1 onwards. Check with{" "}
                <span className="font-mono text-xs">uname -m</span> — “arm64” means Apple
                Silicon, “x86_64” means Intel.
              </CardContent>
            </Card>
          )}

          {/* Ordered by who is looking: a visitor on Android wants the app,
              and everybody else wants the agent for the machine they are on. */}
          {onAndroid && androidBuilds.length > 0 && (
            <AndroidSection builds={androidBuilds} />
          )}

          <div className="flex flex-col gap-3">
            <div>
              <h2 className="text-base font-semibold">Desktop agents</h2>
              <p className="text-sm text-muted-foreground">
                A headless binary for the machine you want to watch. It collects that
                machine's real metrics and pushes them outbound over an encrypted WebSocket —
                it opens no inbound ports, needs no elevated privileges, and has no interface
                of its own. You look at what it reports here.
              </p>
            </div>

            {myDesktop.length > 0 ? (
              myDesktop.map((build) => <BuildCard key={build.filename} build={build} />)
            ) : (
              !onAndroid && <NoBuild platform={platform} catalog={catalog} />
            )}

            {otherDesktop.length > 0 && (
              <>
                <h3 className="text-sm font-medium text-muted-foreground">Other platforms</h3>
                {otherDesktop.map((build) => (
                  <BuildCard key={build.filename} build={build} />
                ))}
              </>
            )}
          </div>

          {!onAndroid && androidBuilds.length > 0 && <AndroidSection builds={androidBuilds} />}

          {catalog.builds.length > 0 && (
            <p className="text-xs text-muted-foreground">
              Builds published{" "}
              {catalog.generated_at
                ? new Date(catalog.generated_at).toLocaleString()
                : "at an unrecorded time"}
              . Missing platforms:{" "}
              {ALL_OS.filter((os) => !catalog.builds.some((b) => b.os === os))
                .map((os) => OS_LABEL[os])
                .join(", ") || "none"}
              .
            </p>
          )}
        </>
      )}
    </AppLayout>
  )
}
