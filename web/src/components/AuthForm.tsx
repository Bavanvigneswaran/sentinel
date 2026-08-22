import type { ReactNode } from "react"

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

interface AuthFormProps {
  title: string
  description: string
  children: ReactNode
  footer: ReactNode
}

/** Shared chrome for the login and signup pages. */
export function AuthForm({ title, description, children, footer }: AuthFormProps) {
  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-6 p-6">
      <div className="flex items-center gap-2">
        <span className="text-lg font-semibold tracking-tight">Sentinel</span>
      </div>

      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
        <CardContent>{children}</CardContent>
      </Card>

      <p className="text-sm text-muted-foreground">{footer}</p>
    </div>
  )
}
