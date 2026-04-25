# Why Kimi is the next adapter (after Gemini)

**Status:** planned, not in flight. This doc captures the rationale for prioritizing Kimi over the other adapters in the planned list (GLM, DeepSeek, generic API).

## The argument

Kimi CLI is architecturally the most distinct mainstream coding CLI on the market. It has first-class swarm primitives (300 sub-agents), an ACP protocol for IDE integration, native skills, MCP support, and its own conception of "agent" that doesn't map cleanly onto either Codex's App Server or Gemini's request-loop model. Adapting it forces us to confront the question: **what does it mean to wrap a CLI that already has its own multi-agent model?**

If our abstraction handles Kimi cleanly, three things become true:

1. **GLM, DeepSeek, Qwen become trivially easy.** They're all simpler integration models than Kimi. The N+1, N+2, N+3 adapters all benefit from generalizations we were forced to make for Kimi.

2. **We discover the right boundary between "host's agent model" and "anyteam's team model."** Right now the implicit assumption is that the host is "dumb" about teams and `claude-anyteam` adds the team layer. Kimi violates that assumption — it has its own team layer. Resolving this cleanly is the most important architectural decision left to make, and it's better made now (with one Kimi case to reason about) than later (with five adapters silently disagreeing about how to handle it).

3. **We earn the right to claim "any CLI."** Right now the README says "any LLM" but the implicit footnote is "as long as the CLI looks vaguely like Codex or Gemini." Kimi makes the claim real.

## What this means concretely for sequencing

- **Kimi before GLM/DeepSeek/Qwen.** Even though those would each be a faster individual ship, doing them first locks in assumptions that won't survive contact with Kimi.
- **Kimi before the generic API adapter.** A generic API adapter is a different concept — it wraps endpoints, not CLIs. Kimi answers the harder of the two questions: how `claude-anyteam` composes with a CLI that already has its own swarm.
- **Don't take Kimi while Gemini's documented limitations are still open.** Specifically: ACP `session/new`+`session/prompt` round-trip viability, MCP tool result coverage gaps. Closing those informs the Kimi ACP integration directly.

## Open architectural questions Kimi will force us to answer

- When Kimi exposes 300 sub-agents and `claude-anyteam` exposes a team of ~5 teammates, who owns the agent abstraction? Does a Kimi teammate count as one teammate (and Kimi internally fans out)? Or does each Kimi sub-agent surface as its own teammate?
- Kimi's ACP and Gemini's ACP overlap in name but not in semantics. How much can `jsonrpc_stdio.py` and the ACP transport layer be reused vs. needing per-vendor specialization?
- Kimi has native skills — are those visible to the host's skill system, hidden behind the adapter, or surfaced as additional tool callables? Each option has correctness implications.
- Authentication / sign-in detection (the work currently in flight on `feat/installer-onboarding`) needs a Kimi-specific probe path — what does Kimi's auth state look like on disk, and does the installer's hand-holding pattern apply unchanged?

## Not in scope for this doc

This is the **why**, not the **how**. The actual integration design — Plan A (headless) vs Plan B (ACP), capability probe shape, prompt engineering, model/effort mapping, test plan — is a separate research deliverable when Kimi work begins. The pattern from the Gemini integration (research → empirical probe → design → implement → audit, with mixed-backend task force) is a good template.

## Trigger to start

Open this work after PR #8 (Gemini Round 4) and `feat/installer-onboarding` are both merged and the Gemini ACP gaps have either closed or been honestly documented as deferred. Don't start during an active Gemini follow-up cycle.
