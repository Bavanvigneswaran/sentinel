import { registerRootComponent } from "expo"

import App from "./App"
import { bootstrapAuth } from "@/stores/auth"

// Restore the session before anything renders. Deliberately at module scope
// rather than in a component effect: React StrictMode double-invokes effects
// in development, and two concurrent /auth/refresh calls presenting the same
// cookie are indistinguishable from a stolen token — the backend would
// correctly revoke the whole family and log the user out on every launch.
// bootstrapAuth() is internally single-flighted, so this also stays correct
// across a Fast Refresh.
void bootstrapAuth()

registerRootComponent(App)
