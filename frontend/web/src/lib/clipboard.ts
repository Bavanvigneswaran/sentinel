/**
 * Copy-to-clipboard that works where this console is actually reached.
 *
 * `navigator.clipboard` is gated on a **secure context**: https, or an origin
 * the browser treats as trustworthy (localhost / 127.0.0.1). `make serve`
 * binds to the LAN, so the real address is `http://192.168.x.y:8000` — not
 * loopback, not https — and there the whole `clipboard` object is `undefined`,
 * not merely a method that rejects.
 *
 * The previous call site read:
 *
 *     navigator.clipboard?.writeText(code).then(ok).catch(fail)
 *
 * which looks defensive and is the bug: optional chaining short-circuits the
 * *entire* chain, so on a LAN origin `.then`/`.catch` were never reached, no
 * promise existed, and pressing Copy did precisely nothing — no copy, no
 * error, no feedback. Silence is the worst of the three outcomes.
 *
 * `document.execCommand("copy")` is deprecated but is not gated on a secure
 * context and is still implemented by every browser this project targets. It
 * is the fallback, not the primary: where the modern API works it is used.
 *
 * Returns whether the text actually reached the clipboard, so a caller can
 * tell the user to select and copy by hand instead of claiming success.
 */

/** The deprecated path. Synchronous, and works on a plain-http LAN origin. */
function copyViaExecCommand(value: string): boolean {
  if (typeof document === "undefined") return false

  const textarea = document.createElement("textarea")
  textarea.value = value
  // Off-screen rather than `display: none` — a hidden element cannot be
  // focused, and execCommand copies the *selection*, so an unfocusable node
  // copies nothing. readOnly stops the mobile keyboard appearing.
  textarea.setAttribute("readonly", "")
  textarea.style.position = "fixed"
  textarea.style.top = "-9999px"
  textarea.style.left = "-9999px"
  textarea.style.opacity = "0"

  const previous = document.activeElement
  document.body.appendChild(textarea)
  try {
    textarea.select()
    textarea.setSelectionRange(0, value.length)
    return document.execCommand("copy")
  } catch {
    return false
  } finally {
    textarea.remove()
    // Put focus back where the user left it, so copying does not move it off
    // the button they just pressed.
    if (typeof HTMLElement !== "undefined" && previous instanceof HTMLElement) previous.focus()
  }
}

export async function copyText(value: string): Promise<boolean> {
  // Both halves of the guard matter: Firefox exposes `navigator.clipboard`
  // with only `readText` missing in some configurations, and a non-secure
  // context omits the object entirely.
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value)
      return true
    } catch {
      // Permission denied, or a document that is not focused. Fall through —
      // execCommand often still succeeds where the async API refuses.
    }
  }
  return copyViaExecCommand(value)
}
