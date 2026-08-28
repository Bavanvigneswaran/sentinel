import type { ReactNode } from "react"

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

interface AuthFormProps {
  title: string
  description: string
  children: ReactNode
  footer: ReactNode
  /** Namespaces the data-testid hooks, e.g. "login" -> login-card. */
  testId: string
}

/** Shared chrome for the login and signup pages. */
export function AuthForm({ title, description, children, footer, testId }: AuthFormProps) {
  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-6 p-6">
      <div className="flex items-center gap-2">
        <span className="text-lg font-semibold tracking-tight">Sentinel</span>
      </div>

      <Card className="w-full max-w-sm" data-testid={`${testId}-card`}>
        <CardHeader>
          <CardTitle data-testid={`${testId}-title`}>{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
        <CardContent>{children}</CardContent>
      </Card>

      <p className="text-sm text-muted-foreground" data-testid={`${testId}-footer`}>
        {footer}
      </p>
    </div>
  )
}
