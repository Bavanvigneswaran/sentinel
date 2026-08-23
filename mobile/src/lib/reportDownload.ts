/**
 * Downloading a rendered report on the phone, and handing it to the OS.
 *
 * The web console downloads a PDF/CSV as a blob and lets the browser save it.
 * A phone has no downloads bar, so "download" here means two steps: write the
 * bytes to app storage, then open the system share sheet so the file can go
 * wherever the person actually wants it — Drive, Gmail, Files. A file saved
 * into the app's sandbox and never surfaced would be a download in name only.
 *
 * `File.downloadFileAsync` streams straight to disk in native code rather than
 * pulling a PDF through the JS bridge as base64, and it *throws* on a non-2xx
 * (UnableToDownloadException) rather than writing the error body into the
 * file — which matters, because a 4KB HTML error page saved as `report.pdf`
 * is exactly the kind of plausible-looking wrong artefact this project keeps
 * refusing to produce.
 *
 * The endpoints are the same two the web console uses; nothing was added to
 * the backend for this.
 */

import { Directory, File, Paths } from "expo-file-system"
import * as Sharing from "expo-sharing"

import { API_BASE_URL } from "@/config"
import { NetworkError, refreshSession } from "@/lib/api"
import { useAuth } from "@/stores/auth"

export type ReportFormat = "pdf" | "csv"

/** Cache, not documents: the file exists to be handed to the share sheet, and
 * the OS is welcome to reclaim it afterwards. Keeping report snapshots in
 * permanent app storage would also be the one place in this system that stores
 * a rendered report, which Phase 9 deliberately does not do. */
function destination(): Directory {
  const dir = new Directory(Paths.cache, "reports")
  if (!dir.exists) dir.create({ intermediates: true })
  return dir
}

function filenameFor(format: ReportFormat, periodDays: string, deviceName?: string): string {
  const scope = deviceName ? deviceName.replace(/[^A-Za-z0-9._-]+/g, "-") : "fleet"
  return `sentinel-${scope}-${periodDays}d.${format}`
}

export interface DownloadedReport {
  file: File
  filename: string
  /** False when the platform has no share sheet at all; the file is still on
   * disk and its path is worth showing rather than pretending nothing happened. */
  shared: boolean
}

export async function downloadReport(options: {
  format: ReportFormat
  periodDays: string
  deviceId?: string
  deviceName?: string
}): Promise<DownloadedReport> {
  const { format, periodDays, deviceId, deviceName } = options

  const query = new URLSearchParams({ period_days: periodDays })
  if (deviceId) query.set("device_id", deviceId)
  const url = `${API_BASE_URL}/reports/export.${format}?${query.toString()}`

  const filename = filenameFor(format, periodDays, deviceName)
  const target = new File(destination(), filename)
  // Re-downloading the same period must overwrite rather than fail or, worse,
  // hand back last week's numbers under this week's name.
  if (target.exists) target.delete()

  const attempt = async (token: string | null): Promise<File> =>
    File.downloadFileAsync(url, target, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })

  let file: File
  try {
    file = await attempt(useAuth.getState().accessToken)
  } catch (first) {
    // The native module reports every failure as one exception type, so a 401
    // is not distinguishable from a dead socket here the way it is in
    // apiFetch. Refreshing and retrying once covers the expired-token case
    // that a long-lived screen actually hits, and costs one wasted request in
    // the cases it does not.
    const session = await refreshSession()
    if (!session) throw first instanceof Error ? first : new NetworkError()
    file = await attempt(session.access_token)
  }

  const shared = await Sharing.isAvailableAsync()
  if (shared) {
    await Sharing.shareAsync(file.uri, {
      mimeType: format === "pdf" ? "application/pdf" : "text/csv",
      dialogTitle: filename,
      UTI: format === "pdf" ? "com.adobe.pdf" : "public.comma-separated-values-text",
    })
  }

  return { file, filename, shared }
}
