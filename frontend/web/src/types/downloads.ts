/** Mirrors app/schemas/downloads.py. */

export type BuildOs = "macos" | "linux" | "windows" | "android"

export interface AgentBuild {
  os: BuildOs
  arch: "x64" | "arm64" | "universal"
  version: string
  filename: string
  size_bytes: number
  sha256: string
  /** False means Gatekeeper or SmartScreen will interrupt the install. */
  signed: boolean
  signing: string
  built_at: string | null
  /** Absolute when a release host is configured; otherwise a path on this API. */
  download_url: string
}

export interface AgentDownloads {
  configured: boolean
  generated_at: string | null
  builds: AgentBuild[]
  /** Non-null whenever `builds` is empty — why, in words the page can render. */
  unavailable_reason: string | null
  source_build_command: string
}
