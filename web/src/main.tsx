import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { RouterProvider } from "react-router"

import "./index.css"
import { router } from "@/routes"
import { bootstrapAuth } from "@/stores/auth"

// Called at module scope, deliberately not inside a component effect: React 19
// StrictMode double-invokes effects in development, and two concurrent
// /auth/refresh calls with the same cookie would look like token theft to the
// backend and revoke the whole family. bootstrapAuth is also internally
// single-flighted, so this stays correct under HMR.
void bootstrapAuth()

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
)
