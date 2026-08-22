import { useState } from "react"
import type { FormEvent } from "react"
import { Link, useLocation, useNavigate } from "react-router"

import { AuthForm } from "@/components/AuthForm"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ApiError } from "@/lib/api"
import { useAuth } from "@/stores/auth"

export function LoginPage() {
  const login = useAuth((s) => s.login)
  const navigate = useNavigate()
  const location = useLocation()

  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login({ email, password })
      const from = (location.state as { from?: string } | null)?.from
      navigate(from ?? "/", { replace: true })
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 429
          ? "Too many attempts. Please wait a moment and try again."
          : err instanceof ApiError
            ? err.message
            : "Could not reach the server.",
      )
      setSubmitting(false)
    }
  }

  return (
    <AuthForm
      title="Sign in"
      description="Access your monitored devices."
      footer={
        <>
          No account?{" "}
          <Link to="/signup" className="text-foreground underline-offset-4 hover:underline">
            Create one
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
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>

        <Button type="submit" disabled={submitting}>
          {submitting ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </AuthForm>
  )
}
