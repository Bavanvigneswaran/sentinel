import { useEffect, useState } from "react"
import type { FormEvent } from "react"
import { Link, useNavigate, useSearchParams } from "react-router"

import { AuthForm } from "@/components/AuthForm"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { PasswordInput } from "@/components/ui/password-input"
import { ApiError } from "@/lib/api"
import { useAuth } from "@/stores/auth"

/** Mirrors password_min_length in backend/app/config.py. */
const PASSWORD_MIN_LENGTH = 12

export function ResetPasswordPage() {
  const resetPassword = useAuth((s) => s.resetPassword)
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()

  // Captured once on mount via a lazy initialiser, because the effect below
  // removes it from the URL and a re-read after that would find nothing.
  const [token] = useState(() => searchParams.get("token") ?? "")

  const [password, setPassword] = useState("")
  const [confirm, setConfirm] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  /**
   * Strip the token from the address bar as soon as it has been read.
   *
   * It is a live credential until it is spent, and a query string is the one
   * place a credential reliably leaks — into browser history, into the
   * `Referer` on any outbound link, and over the shoulder. replaceState rather
   * than a navigation so the router does not remount this page and lose the
   * ref above.
   */
  useEffect(() => {
    if (!token) return
    window.history.replaceState(null, "", window.location.pathname)
  }, [token])

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)

    // Both checked client-side purely for a fast, friendly message; the server
    // enforces the policy regardless.
    if (password.length < PASSWORD_MIN_LENGTH) {
      setError(`Password must be at least ${PASSWORD_MIN_LENGTH} characters.`)
      return
    }
    if (password !== confirm) {
      setError("The two passwords don't match.")
      return
    }

    setSubmitting(true)
    try {
      await resetPassword({ token, password })
      navigate("/", { replace: true })
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 429
          ? "Too many attempts. Please wait a few minutes and try again."
          : err instanceof ApiError
            ? err.message
            : "Could not reach the server.",
      )
      setSubmitting(false)
    }
  }

  return (
    <AuthForm
      testId="reset"
      title="Choose a new password"
      description="This signs you in and ends every other session on this account."
      footer={
        <>
          Changed your mind?{" "}
          <Link
            to="/login"
            data-testid="reset-to-login"
            className="text-foreground underline-offset-4 hover:underline"
          >
            Back to sign in
          </Link>
        </>
      }
    >
      {token ? (
        <form onSubmit={handleSubmit} className="flex flex-col gap-4" data-testid="reset-form">
          {error && (
            <Alert variant="destructive" data-testid="reset-error">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          <div className="flex flex-col gap-2">
            <Label htmlFor="password">New password</Label>
            <PasswordInput
              id="password"
              data-testid="reset-password"
              autoComplete="new-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="confirm">Confirm new password</Label>
            <PasswordInput
              id="confirm"
              data-testid="reset-confirm"
              autoComplete="new-password"
              required
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
          </div>

          <Button type="submit" disabled={submitting} data-testid="reset-submit">
            {submitting ? "Saving…" : "Set new password"}
          </Button>
        </form>
      ) : (
        // Reached by opening /reset-password directly, or by a mail client that
        // mangled the link. Says what to do rather than presenting a form that
        // cannot succeed.
        <Alert variant="destructive" data-testid="reset-no-token">
          <AlertDescription>
            This page needs the link from your reset email. Open that link directly, or{" "}
            <Link to="/forgot-password" className="underline underline-offset-4">
              request a new one
            </Link>
            .
          </AlertDescription>
        </Alert>
      )}
    </AuthForm>
  )
}
