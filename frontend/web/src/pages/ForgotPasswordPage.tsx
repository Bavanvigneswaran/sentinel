import { useState } from "react"
import type { FormEvent } from "react"
import { Link } from "react-router"

import { AuthForm } from "@/components/AuthForm"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ApiError, apiFetch } from "@/lib/api"

export function ForgotPasswordPage() {
  const [email, setEmail] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [sent, setSent] = useState(false)

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await apiFetch<void>("/auth/forgot-password", {
        method: "POST",
        body: { email },
        anonymous: true,
      })
      setSent(true)
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 429
          ? "Too many requests. Please wait a few minutes and try again."
          : err instanceof ApiError
            ? err.message
            : "Could not reach the server.",
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthForm
      testId="forgot"
      title="Reset your password"
      description="We'll email you a link to choose a new one."
      footer={
        <>
          Remembered it?{" "}
          <Link
            to="/login"
            data-testid="forgot-to-login"
            className="text-foreground underline-offset-4 hover:underline"
          >
            Back to sign in
          </Link>
        </>
      }
    >
      {sent ? (
        // Deliberately says "if", and says the same thing for an address with
        // no account: the server answers both identically so this endpoint
        // cannot be used to find out who has an account here, and a confident
        // "sent!" on this page would give that away where the API does not.
        <Alert data-testid="forgot-sent">
          <AlertDescription>
            If an account exists for that address, a reset link is on its way. The link works
            once and expires in 30 minutes. Check your spam folder if it doesn't arrive.
          </AlertDescription>
        </Alert>
      ) : (
        <form onSubmit={handleSubmit} className="flex flex-col gap-4" data-testid="forgot-form">
          {error && (
            <Alert variant="destructive" data-testid="forgot-error">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          <div className="flex flex-col gap-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              data-testid="forgot-email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>

          <Button type="submit" disabled={submitting} data-testid="forgot-submit">
            {submitting ? "Sending…" : "Email me a link"}
          </Button>
        </form>
      )}
    </AuthForm>
  )
}
