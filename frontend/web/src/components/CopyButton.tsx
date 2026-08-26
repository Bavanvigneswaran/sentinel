import { useEffect, useRef, useState } from "react"

import { Button } from "@/components/ui/button"
import { copyText } from "@/lib/clipboard"
import { cn } from "@/lib/utils"

const FEEDBACK_MS = 1600

/**
 * Copy one string, and say whether it worked.
 *
 * The failure branch is the reason this is a component rather than an
 * onClick: a clipboard write can genuinely be refused (a locked-down
 * enterprise policy, a document that lost focus), and the honest thing is to
 * tell the user to select the text by hand — not to flash "Copied" over a
 * clipboard that still holds whatever was in it before.
 */
export function CopyButton({
  value,
  label = "Copy",
  size = "sm",
  variant = "outline",
  className,
  disabled,
}: {
  value: string
  label?: string
  size?: "sm" | "default"
  variant?: "outline" | "ghost" | "default"
  className?: string
  disabled?: boolean
}) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle")
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [])

  const handleClick = async () => {
    const ok = await copyText(value)
    setState(ok ? "copied" : "failed")
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => setState("idle"), FEEDBACK_MS)
  }

  return (
    <Button
      type="button"
      size={size}
      variant={variant}
      disabled={disabled}
      onClick={() => void handleClick()}
      className={cn(state === "failed" && "text-destructive", className)}
      // Announced to assistive tech, which otherwise gets no signal that
      // anything happened at all.
      aria-live="polite"
    >
      {state === "copied" ? "Copied" : state === "failed" ? "Select it by hand" : label}
    </Button>
  )
}

/**
 * A shell command in a box with its own copy button.
 *
 * One command per box on purpose. The steps have to be run in order and each
 * one can fail in its own way; a single blob of four lines pasted into a
 * terminal runs them all before the user has seen the first one's output.
 */
export function CopyableCommand({ command }: { command: string }) {
  return (
    <div className="flex items-stretch gap-2">
      <code className="flex-1 overflow-x-auto whitespace-pre rounded bg-muted px-3 py-2 font-mono text-xs leading-6">
        {command}
      </code>
      <CopyButton value={command} className="h-auto shrink-0" />
    </div>
  )
}
