# Home AI (HAI)

Home AI is the central intelligence layer of the homelab: an agentic orchestrator that executes
complex tasks plus a friend-onboarding system that manages external user access. This domain covers
the internal architecture of the LLM execution engine, autonomous self-engineering capabilities, and
the infrastructure to securely onboard and manage guest users.

## Common questions

- How does the orchestrator handle multi-step agentic loops and tool usage? → see [[HAI-LLM-Execution-Context-Mechanics]]
- What are the core API contracts and internal components (Kirk/Spock)? → see [[HAI-Core-Architecture-API-Contracts]]
- How can I trigger or verify autonomous self-engineering behaviors? → see [[HAI-Autonomous-Behavior-Self-Engineering]]
- What is the lifecycle for deploying new components via sidecars? → see [[HAI-Deployment-Lifecycle-Sidecars]]
- How do I perform operational testing or probe specific tiers? → see [[HAI-Operations-Testing-Known-Issues]]
- How does the self-serve process work for friends joining the media stack? → see [[Friend-Architecture-Flow]]
- What permissions and capabilities are exposed to onboarded friends? → see [[Friend-Capabilities-Permissions]]
- How is security enforced for friend infrastructure (DNS, firewall)? → see [[Friend-Infrastructure-Security]]

## Pages in this domain

- **[[HAI-Core-Architecture-API-Contracts]]** — Core orchestrator architecture, API contracts, and internal naming conventions.
- **[[HAI-LLM-Execution-Context-Mechanics]]** — LLM execution signature, agentic tool-loop limits, and context handling.
- **[[HAI-Autonomous-Behavior-Self-Engineering]]** — Regex gates and admin checks required for autonomous self-engineering behaviors.
- **[[HAI-Deployment-Lifecycle-Sidecars]]** — Deployment endpoints, lifecycle states, and sidecar integration via hostops.
- **[[HAI-Operations-Testing-Known-Issues]]** — Operational probing, testing strategies, and known issues.
- **[[Friend-Architecture-Flow]]** — Self-serve onboarding flow for friends, including media access and Discord membership.
- **[[Friend-Capabilities-Permissions]]** — Capabilities and HAI orchestrator access granted to onboarded users.
- **[[Friend-Administration-UI]]** — Admin panel features, including impersonation links and preview capabilities.
- **[[Friend-Infrastructure-Security]]** — Security model for friend infrastructure: DNS-only operation and firewall rules.
- **[[Friend-Operations-History]]** — Default component configuration during user import, and operational history.
