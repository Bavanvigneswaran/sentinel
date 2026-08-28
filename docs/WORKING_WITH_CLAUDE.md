# Getting this built without burning your limits

## Which model, when

| Use | Model |
|---|---|
| Architecture, DB schema, auth design, agent protocol, WS/concurrency, ML model design, hard debugging, security review | **Opus 5** (this one) |
| ~80% of the work: endpoints, React components, collectors, tests, refactors, wiring | **Sonnet 5** |
| Mechanical edits, renames, config files, doc updates, log grepping | **Haiku 4.5** |

Switch models in the app's model picker. Turn on **`/fast`** (Opus with faster output) for mechanical work —
it's the same Opus, not a downgrade.

The pattern that works: **plan with Opus, build with Sonnet.** Enter plan mode (Shift+Tab), let Opus produce
the plan, approve it, then switch to Sonnet to execute. Planning is cheap; re-doing a bad design is not.

## The ten habits that actually save budget

1. **One phase per session, `/clear` between.** A session that starts fresh with a good CLAUDE.md costs a
   fraction of one that's been running for six hours.
2. **Keep CLAUDE.md current.** It's loaded every session. Stack, commands, conventions, gotchas. Cheapest
   context you will ever buy.
3. **Contract first.** Define Pydantic models / the OpenAPI spec before frontend and backend work. Generate
   TS types from it. Then a frontend session never needs backend code in context, and vice versa.
4. **Never paste raw logs.** `grep -n ERROR logs/app.log | tail -20`. Let Claude run the command itself
   rather than you round-tripping thousands of tokens of output.
5. **Let Claude run the tests.** Don't paste failures — say "run the test suite and fix what fails."
6. **Commit at every green state.** A session that goes sideways becomes `git reset --hard`, not a long,
   expensive repair conversation. `git init` this repo before Phase 0.
7. **Small files.** Under ~400 lines. Big files mean big reads on every edit.
8. **Don't spawn subagents** unless a search is genuinely broad and fan-out shaped. Each one starts cold and
   re-derives context you already have — it's the expensive path.
9. **Batch your questions.** One message with five decisions beats five messages.
10. **Spend review budget where it matters.** `/security-review` after Phase 1 and Phase 2.
    `/code-review high` on the alert state machine and the agent protocol. Skip it on UI components.

## Session-start template

> Read CLAUDE.md and docs/ROADMAP.md. We're starting Phase N. Plan it first, then implement.
> Don't touch anything outside `<the relevant dirs>`.

## Scope discipline

Two failure modes will cost you more than model choice ever will:

- **Building the AI features before the data pipeline is solid.** Phase 3 first. Real numbers on a real chart.
- **Polishing UI before the feature works.** shadcn/ui already looks professional. Make it correct, then pretty.

## Incident insights need no API key

Phase 8 originally called the Claude API (Haiku 4.5 for summaries, Sonnet 5 for root-cause) and this
section told you to put an `ANTHROPIC_API_KEY` in `backend/.env`. It no longer does, and there is no
key to set: `app/insights/generator.py` renders both texts locally from the signal bundle, so the
product depends on no hosted AI API and costs nothing per incident. A leftover `ANTHROPIC_API_KEY` in
an existing `.env` is ignored, not read.

The caching advice survived the change and is still worth following — see
`analysis/incidents.py`'s `correlation_fingerprint()`. It no longer saves money; it keeps
`summary_generated_at` meaning "when this explanation was reached" instead of "when a sweep last ran".
