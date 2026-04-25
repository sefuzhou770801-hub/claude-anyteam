# claude-anyteam: Strategic Roadmap

## The Bet

Multi-agent collaboration is the dominant paradigm for agentic coding within 18–24 months. Single-agent loops will feel as outdated as single-threaded compilation. The Agent Teams pattern — a lead orchestrator coordinating peer agents with shared task lists, direct messaging, and per-agent context windows — is the structural breakthrough that makes complex codebases tractable for AI.

If that's right, the open question isn't *whether* multi-agent coding wins. It's *whether the multi-agent layer is open or closed*. Every major vendor has incentives to keep teams single-vendor (a coordinated swarm of their own model is a moat; a heterogeneous team is not). The window for an open interop standard is the present moment, while vendors are competing for developer mindshare and openness is strategically rational. That window closes the moment one vendor consolidates dominance.

claude-anyteam exists to be the open interop layer that makes that window stay open.

## North Star

Be the protocol that defines how heterogeneous coding agents collaborate. Win developer mindshare while the category is forming. Make multi-vendor agent teams the obvious default before any single vendor can lock the pattern down.

## Guiding Principles

- **Speed beats polish.** The category is forming. The first credible open standard with working implementations wins disproportionate mindshare. Ship.
- **Protocol > product.** Every decision asks: "does this strengthen the spec and accelerate adoption?"
- **Open as strategic necessity, not philosophy.** Closed protocols don't propagate. The moat is adoption, not code.
- **Neutralize host-dependence early.** Currently bound to Claude Code's Agent Teams. Long-term survival requires being host-agnostic before any host turns hostile.
- **Move faster than vendors can close.** The default outcome is vendor lock-in. The intervention is being too widely adopted to ignore by the time anyone tries.

## Phase 1: Establish Credibility (0–60 days)

**Goal:** Ship a hardened reference implementation that proves multi-vendor Agent Teams is real, working, and meaningfully better than the alternatives.

- Cut v1.0 of the Codex adapter with a public stability commitment and semver guarantees.
- Harden the Gemini CLI adapter to the same bar.
- Ship the Kimi adapter as #3. Rationale: Kimi CLI is the most architecturally distinct mainstream coding CLI (native swarm primitives, skills, ACP). Building it third forces the right generalizations into the abstraction layer while the codebase is still small enough to refactor freely. Every adapter after Kimi gets dramatically faster.
- Publish reproducible benchmarks. Concrete claims of the form: "On task class X, a heterogeneous N-agent team (Claude lead + Codex + Kimi + Gemini) produces Y outcome at Z% the cost of an all-Claude team of equivalent size." Include scripts, raw logs, methodology. This is the primary marketing artifact for the entire project.
- Set up nightly CI against the latest Claude Code release. The protocol is reverse-engineered; breakage detection has to be automated from day one.
- Rewrite the README around the actual value prop: *Stack the best agents from every vendor into one team. The orchestrator picks the right agent for each subtask. Costs less, runs longer, builds better than any single-vendor alternative.*

**Exit criteria:** v1.0 tag, three working adapters spanning the full architectural range, benchmark that's defensible enough to lead with, automated regression detection.

## Phase 2: Claim the Category (months 2–5)

**Goal:** Stop being a Claude Code plugin. Become the recognized interop standard for multi-vendor agent collaboration.

- Extract and publish the protocol specification. Document the abstraction over mailbox I/O, atomic task claims, idle notifications, shutdown lifecycle, mid-task steering, cross-task memory, and the host-agnostic boundary. License the spec permissively (CC-BY).
- Rename. Vendor-neutral umbrella; `claude-anyteam` becomes the Claude Code-specific binding. The current name caps the project's ceiling.
- Stand up a docs site that separates protocol docs from binding-specific docs. The protocol pages must make sense to someone who has never used Claude Code.
- Ship GLM and Qwen adapters. Both should be fast given the abstraction work done in Phase 1. Five total adapters by end of Phase 2.
- Begin direct outreach to maintainers of Cursor, OpenCode, Aider, Continue, Sourcegraph Cody. The pitch is not "implement our spec" — it's "let's collaborate on what the standard for multi-vendor agent collaboration should look like." Get them in the room while the spec is still being shaped.
- Publish the architectural blog post explaining why protocol-level integration is structurally superior to API translation, and why heterogeneous teams beat homogeneous ones for non-trivial codebases. Aim it at the developer-tools commentariat. This is the piece that gets the project into the conversation.
- Apply to speak at conferences (LangChain DevDay, AI Engineer Summit, KubeCon's AI track). Mindshare in this category is built in talks, not docs.

**Exit criteria:** Public spec, vendor-neutral name and docs, five adapters, two non-Claude harness maintainers actively engaged with the spec, one accepted conference talk.

## Phase 3: Force the Issue (months 5–10)

**Goal:** Move from "interesting project" to "the obvious answer." Make it costly for major vendors to ignore.

- Ship a non-Claude host binding (Cursor or OpenCode, whichever is more receptive). The moment anyteam runs as a primary integration on a non-Anthropic host, the cross-host story is real instead of theoretical. This is the single most important strategic milestone of the entire roadmap.
- Ship DeepSeek and additional adapters as the abstraction allows. Eight total adapters by end of Phase 3.
- Stand up a hosted control plane for federated auth, unified billing across providers, usage analytics, and audit logs. Open-source binary remains fully functional without it; the hosted plane is convenience, not lock-in. This is the revenue surface.
- Establish a contributor pipeline. Adapter development is the natural path. Document it ruthlessly. Adapter PRs from external contributors should be the primary measure of project health by the end of this phase.
- Publish quarterly state-of-the-ecosystem reports: which adapters work with which hosts, what changed, what's coming. These become the project's calling card to the broader community.
- Begin enterprise conversations. The pitch isn't "buy our SaaS" — it's "your engineering org is going to want multi-vendor agent teams within a year. Here's the open standard that lets you avoid lock-in to any single vendor."

**Exit criteria:** Eight adapters, two host bindings, hosted control plane in public beta, three external contributors merging adapter PRs, two enterprise pilots in flight.

## Phase 4: Hold the Line (months 10–18)

**Goal:** Survive the inevitable consolidation pressure. Convert mindshare into either a viable independent business, an acquisition with leverage, or a foundation-backed standard.

The pre-mortem is now four scenarios, not three:

**Scenario A — A major vendor ships their own multi-vendor teams.** Most likely Anthropic, possibly OpenAI. Their version will be polished but vendor-controlled (curated partner list, opaque selection criteria, friendly to their billing). anyteam's response: lean hard into being the *open* alternative. The pitch becomes "multi-vendor teams without asking permission from a hyperscaler." Cross-host bindings are the survival path; Phase 3 must have made this real.

**Scenario B — Vendors stay neutral; the project gains traction independently.** Raise a Series A ($5–10M) to scale the team to 8–12 engineers. Push hard on spec adoption: RFC-style governance, reference test suites, partnerships with conferences. Begin formal commercial sales motion on the control plane. This is the path to category leadership.

**Scenario C — A vendor actively breaks the integration.** Most likely Anthropic, since the project's flagship binding lives in their codebase. The Phase 3 cross-host story is what makes this survivable. Communicate transparently; the narrative becomes "open ecosystem vs. walled garden." Use the moment to accelerate adoption among neutral parties (Cursor, OpenCode, Sourcegraph) who now have aligned incentives.

**Scenario D — The category fragments before standardizing.** Multiple closed multi-agent systems emerge in parallel; no single one consolidates. anyteam's response: become the bridge between them. Cursor's agents talking to Claude Code's agents talking to Codex's agents. This is harder than being a clean standard but creates a more durable position because the moat is integrations rather than spec authority.

**Exit criteria:** Scenario identified within 30 days of signal. Response plan executing. Project is unambiguously a category leader by adoption metrics, regardless of which scenario unfolded.

## Phase 5: Convert the Position (18+ months)

**Goal:** Realize the value of the position the previous phases built. The right outcome depends on which scenario unfolded.

Three viable destinations:

**Standard-bearer.** anyteam's protocol becomes the de facto interop layer for multi-agent coding. The project transitions to foundation-backed governance (Linux Foundation, CNCF, or a new agent-focused foundation). Commercial entity continues to operate the hosted control plane and enterprise tier. Pattern: Kubernetes/CNCF, OpenTelemetry, OCI. This is the highest-value outcome but requires multi-vendor buy-in that's hard to engineer.

**Acquisition.** Most likely acquirers shift dramatically depending on scenario:
- *Scenario A:* Cursor, Sourcegraph, or GitHub buy anyteam as their multi-vendor strategy against the dominant vendor's closed system.
- *Scenario B:* Anthropic, OpenAI, or Google buy to absorb the standard into their ecosystem.
- *Scenario C:* A neutral developer-tools company (Sourcegraph, GitLab, JetBrains) buys to position against the hostile vendor.
- *Scenario D:* Multiple acquirer paths; auction dynamics likely.

If multi-agent coding becomes dominant as predicted, plausible acquisition range is $50–250M, materially higher than the original roadmap's $15–60M estimate. The category being larger raises both the floor and the ceiling.

**Sustained independence.** Hosted plane and enterprise revenue cross $5M ARR. Project operates as an independent commercial open-source company. Pattern: HashiCorp early years, dbt Labs, Supabase. Less explosive than the other paths but most preserves project autonomy and mission.

## Licensing Stance

- Open source core under MIT or Apache-2.0 indefinitely. The protocol's value scales with adoption, and adoption requires permissive licensing.
- The protocol specification itself stays permissively licensed (CC-BY). Never proprietize the spec, even after acquisition. This is non-negotiable; closing the spec destroys the position the project built.
- If hyperscaler-fork pressure emerges in Phase 4+, the *commercial control plane* may move to BUSL or Elastic License v2. The core, adapters, reference implementation, and spec stay permissive.
- Never close-source the core. The project's entire value is being the open option. Closing it converts a $50–250M asset into a $5M one overnight.

## Key Risks

- **A major vendor ships polished native multi-vendor teams before anyteam achieves mindshare.** This is the single largest risk. Mitigation: Phase 1 and Phase 2 must move fast. Speed of mindshare-acquisition is the primary defense. The cross-host binding in Phase 3 is the survival mechanism if mindshare falls short.
- **Agent Teams pattern doesn't generalize beyond Claude Code.** If Anthropic's specific implementation has properties that don't translate to other harnesses, the spec becomes Claude-Code-shaped and other vendors won't adopt it. Mitigation: design the spec from the abstraction, not the implementation. Get Cursor/OpenCode maintainer input before declaring the spec stable.
- **Protocol drift breaks adapters every Claude Code release.** Mitigated by nightly CI (Phase 1) and dedicated maintenance bandwidth (Phase 3 hires).
- **Subscription-subsidy economics shift.** A core part of the value prop is stacking subsidized subscriptions for cost-effective compute. If providers tighten limits, the marketing pitch weakens. Mitigation: lead with the architectural argument (heterogeneous teams beat homogeneous ones), not the price argument. Price is the gateway; architecture is the moat.
- **Maintainer burnout.** N×M complexity is real. Hire before burning out. The Phase 3 raise exists to prevent this.
- **Audience-too-narrow risk has been resolved by the bet.** If the bet is right, the audience is "every serious agentic coder," not "Claude Code power users." If the bet is wrong, the original roadmap's outcome distribution applies and 60% is "niche tool."

## Success Metrics by Phase

| Phase | Primary metric | Secondary metrics |
|---|---|---|
| 1 | Three adapters at v1.0 quality + benchmark published | Stars, README clarity, CI green rate |
| 2 | Spec published + 2 non-Claude maintainers engaged | Five adapters, conference acceptance, doc traffic |
| 3 | Non-Claude host binding shipped | Eight adapters, external contributor count, hosted-plane signups |
| 4 | Scenario identified + response executing | Adoption metrics across hosts, enterprise pilots, spec implementations outside the project |
| 5 | Outcome realized | Revenue, acquisition value, foundation governance status |

## Revised Outcome Distribution

This is the part of the document that changes most under the bet. The original distribution assumed multi-agent teams remain a power-user feature. If the bet holds:

- 35% — sustained independence as a profitable commercial open-source company. The category is large enough that even capturing a slice supports a real business.
- 30% — acquisition in the $50–250M range. Multiple plausible acquirer paths across all four scenarios.
- 20% — category-defining standard with foundation governance. Requires either explicit vendor partnership or sufficient cross-vendor adoption to force standardization.
- 10% — niche tool that gets superseded by a vendor's native implementation faster than mindshare can be built. The original 60% case, now compressed because the bet shifts the playing field.
- 5% — the bet is wrong; multi-agent coding doesn't become dominant; original roadmap's outcomes apply.

Optimize for the 35% (sustained independence). Preserve optionality for the 30% (acquisition) and 20% (standard). Accept the 10% as the cost of moving on a defensible thesis.

## What Changes If the Bet Is Wrong

If multi-agent coding doesn't become dominant within 24 months, this roadmap reverts toward the original conservative version. The Phase 1 and Phase 2 work is robust to that — adapters and a spec are valuable in either world. The Phase 3 onwards pacing slows; the hosted control plane stays smaller; acquisition math returns to the $15–60M range.

The bet is asymmetric: if right, the upside expands by ~3-5x. If wrong, the downside is roughly the same as the conservative roadmap. That makes it the right bet to make explicit and execute against.

---

*Last updated: 2026-04-25. This document captures a thesis, not a prediction. The thesis should be re-examined every quarter against actual market signals: how often is "multi-agent" appearing in agentic coding discourse, how many harnesses have shipped Agent Teams equivalents, what fraction of serious users are running multi-agent workflows daily.*
