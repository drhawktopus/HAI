# Research note — "OpenCode Got Hacked Badly (My API Keys Got Drained)" (Kai, 2026-10-20): the OpenCode Console breach and API-key spend ceilings

**EXTERNAL SOURCE NOTE — not a lab measurement.** What a third-party YouTube video claims, tagged by how far each claim was checked. This is a **security incident** video, not a model or speed video. The lab does **not** use OpenCode Console (see "Lab relevance"); the only lab mention of OpenCode is as one app harness evaluated and not adopted in [[37-Work-Loop-Harness-Check-2026-10-16]].

Filed 2026-10-20 at the owner's request. Raw files (auto-captions VTT, info JSON, description, cleaned transcript) are in `Claude Home\research\video-0p96nZsRxlY\`.

**Certainty tags:** **[D]** stated in the video, unchecked · **[V]** checked against a primary or independent source · **[L]** compared with a lab fact · **[?]** named but not found.

## The video

| | |
|---|---|
| Title | OpenCode Got Hacked Badly (My API Keys Got Drained) |
| Channel | Kai |
| Uploaded | 2026-10-20 · 19 min 6 s · 12 chapters · 33,258 views at pull time |
| Captions | YouTube auto-generated English; readable, a few garbled names |
| Thesis | One attacker, one stolen key list, bills of $16 to $3,000 — the difference was the spend ceiling on each key, which the owner controls |

## Claims

| # | Claim | Tag | Note |
|---|---|---|---|
| 1 | On 2026-10-17 OpenCode said a "large scale distillation attack" reached a legacy system and exposed OpenAI/Anthropic/Google keys saved in **OpenCode Console** (the BYOK web dashboard); the terminal app was not involved | **[V]** | Independent press (Notebookcheck, OfficeChai, Dealroom) agrees: Console only, ~1.4 % of Console users, app unaffected, cause of entry not disclosed |
| 2 | Reported losses: $1,700 (Preston Zeller), $3,000, $16; Colin Parsons $281 OpenAI + $168 Anthropic; Weinbach drained to $2.15 past a prepaid zero | **[V, partly]** | $1,700 is in the press; the others are individual posts, unchecked |
| 3 | First OpenCode statement came 17.5 h after the exposure window opened (17:56 UTC); OpenAI warned one user 9 h earlier; Anthropic revoked affected Claude keys | **[D]** | Press confirms Anthropic revoked keys; the timings are the video's reading of X posts and are unchecked |
| 4 | The "legacy" database was one a deploy had stopped pointing at: a public commit on 10-12, "remove legacy database from console", deleted the login service and DB password from the new codebase but not the production database; in dev the delete failed three times (deletion protection) | **[D]** | Reading of OpenCode's public repo; not independently checked. Plausible, not proven |
| 5 | Provider keys were stored **plain text** in a `provider.credentials` column; the settings page masks the first 8 and last 8 characters, which only works if the real key is stored | **[D]** | Inference from public schema and UI code; OpenCode has not confirmed the storage format. The masking argument is sound as logic |
| 6 | "Distillation" is a poor label: the first request on a stolen key was a one-token probe ("please reply" → "OK"), then a 35 M-token burn — a key-validation sweep, not training | **[D]** | Quoted from Weinbach and Lambert posts; press notes the label is OpenCode's own and sources differ on framing |
| 7 | Anthropic published new distillation defenses on 10-12 (90–99 % less harvestable), and one rule applies only to API accounts created on/after **2026-08-31**, so aged real-developer keys dodge it | **[V]** | Confirmed by pulse2, unite.ai, implicator.ai: the "preserved thinking" check is on by default for accounts created on/after 8-31 and opt-in for older ones. The 90–99 % figure is Anthropic's own estimate. The "why attackers want aged keys" motive is the video's inference |
| 8 | July: OpenCode banned one person running ~8,000 fraudulent accounts and reselling tokens; NSA/CISA/FBI flagged API-proxy laundering | **[D]** | Not checked |
| 9 | Provider ceilings: OpenAI spend **alert** stops nothing, the **hard limit** is a separate switch and not instantaneous; Anthropic is prepaid but **auto-reload** refills a drained balance, tiers cap $500 / $1,000 / $200,000 a month; Gemini tier 1 caps at $250 a month with billing ~10 min behind | **[D]** | Provider figures not checked against the providers' own pages; verify before relying on any number |

## Advice list (the video's, "do it tonight")

1. Revoke keys **at the provider**, not just in the tool. 2. Rotate every key ever pasted into a third-party tool, email or not (people reported drains with no email). 3. Turn on OpenAI's **hard** limit. 4. Turn off auto-reload on prepaid accounts. 5. Keep balances at an amount you would shrug at. 6. Delete forgotten keys. 7. One key per tool and per service; production keys stay on your own servers.

## Lab relevance

- **The lab does not use OpenCode (checked 2026-10-21, the owner's question):** no `opencode` binary or config directory on Softserve, Sundae, Mothership or BigHonker; no mention in `Reference\Credentials\`; the library shows it only as an evaluated-and-rejected harness ([[37-Work-Loop-Harness-Check-2026-10-16]]) and in a quoted YouTube comment ([[34-A1-Agentic-Build-Family-Spec]]). Rigby was not checked. Whether the owner personally ever pasted a provider key into OpenCode Console is **not in the library** and is the owner's to say.
- **Generalisable to the lab [L]:** any provider API key the lab or the owner has pasted into a third-party hosted dashboard sits in someone else's database. Lab keys live in `Reference\Credentials\` and on the boxes, not in third-party consoles (per the locker rule); the BYOK-in-a-vendor-console pattern is the exposure.
- **Anthropic spend ceiling:** the advice about hard limits and auto-reload applies to any Anthropic or OpenAI API account of the owner's regardless of this incident. Not checked here — account settings are his.
