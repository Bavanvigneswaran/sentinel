import { useState } from "react"
import type { FormEvent } from "react"
import { Link, useNavigate } from "react-router"

import { AuthForm } from "@/components/AuthForm"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ApiError } from "@/lib/api"
import { useAuth } from "@/stores/auth"

/** Mirrors password_min_length in backend/app/config.py. */
const PASSWORD_MIN_LENGTH = 12

export function SignupPage() {
  const signup = useAuth((s) => s.signup)
  const navigate = useNavigate()

  const [displayName, setDisplayName] = useState("")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)

    // Checked client-side purely for a fast, friendly message; the server
    // enforces it regardless.
    if (password.length < PASSWORD_MIN_LENGTH) {
      setError(`Password must be at least ${PASSWORD_MIN_LENGTH} characters.`)
      return
    }

    setSubmitting(true)
    try {
      await signup({ email, password, display_name: displayName.trim() || null })
      navigate("/", { replace: true })
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 409
          ? "That email is already registered."
          : err instanceof ApiError
            ? err.message
            : "Could not reach the server.",
      )
      setSubmitting(false)
    }
  }

  return (
    <AuthForm
      title="Create an account"
      description="Start monitoring your infrastructure."
      footer={
        <>
          Already registered?{" "}
          <Link to="/login" className="text-foreground underline-offset-4 hover:underline">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <div className="flex flex-col gap-2">
          <Label htmlFor="displayName">Name</Label>
          <Input
            id="displayName"
            autoComplete="name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
          />
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            autoComplete="new-password"
            required
            minLength={PASSWORD_MIN_LENGTH}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            At least {PASSWORD_MIN_LENGTH} characters.
          </p>
        </div>

        <Button type="submit" disabled={submitting}>
          {submitting ? "Creating account…" : "Create account"}
        </Button>
      </form>
    </AuthForm>
  )
}
