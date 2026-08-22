import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"

function App() {
  const [status, setStatus] = useState<"checking" | "ok" | "down">("checking")

  useEffect(() => {
    fetch("/api/health")
      .then((res) => (res.ok ? res.json() : Promise.reject()))
      .then(() => setStatus("ok"))
      .catch(() => setStatus("down"))
  }, [])

  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-4 bg-neutral-950 text-neutral-100">
      <h1 className="text-2xl font-medium">Sentinel</h1>
      <p className="text-sm text-neutral-400">
        Backend:{" "}
        <span
          className={
            status === "ok"
              ? "text-emerald-400"
              : status === "down"
                ? "text-red-400"
                : "text-neutral-400"
          }
        >
          {status}
        </span>
      </p>
      <Button variant="outline" onClick={() => window.location.reload()}>
        Re-check
      </Button>
    </div>
  )
}

export default App
