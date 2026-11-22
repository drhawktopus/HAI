# Project #23 — Voice Interface to Claude ("Talk to Claude while driving")

**Design goal:** Conversational, hands-free voice interaction with Claude while driving or multitasking remotely. The interface supports barge-in and audio replies.

**Integration constraint:** Uses the user’s existing Claude Code conversations on a Max subscription; does not use the pay-per-token API.

**Ownership:** Claude + the owner.

## 1. Goal & success criteria

**Goal:** The system enables hands-free, eyes-free spoken interaction with Softserve Claude Code via car Bluetooth while the owner is driving.

When joined from a Discord voice channel using the phone's interface, speech-to-text (STT) and text-to-speech (TTS) must run locally on user-owned hardware to answer questions aloud within seconds. The session manager must resume existing Softserve sessions and allow interruption of ongoing spoken replies mid-stream.

**Explicit non-goals:** Implementation does not include HAI as the brain, a native phone app distribution, or fully-local audio transport (Discord servers handle audio transport).

## 2. Architecture

```
  ┌──────────┐   Discord voice    ┌─────────────────────────────────┐   HTTP    ┌────────────────────┐
  │  Phone    │◀── (app, car ◄── ▶│  VOICE BOT  (discord.js)          │  /ask    │  Softserve          │
  │ Discord   │    Bluetooth     │  jukebox sibling, always-on host │─────────▶│  claude-bridge shim │
  │  app      │                    │  • @discordjs/voice PLAY (TTS out)│          │  wraps: claude -p    │
  └──────────┘                    │  • @discordjs/voice RECEIVE (STT in)         │  --resume <session>  │
                                   │  • VAD / turn-taking / barge-in   │◀─────────│  (the owner's Max login)  │
                                   │  • brain router (Claude→HAI later)│  reply    └─────────┬──────────┘
                                   └───────┬───────────────┬──────────┘                     │ same session
                                           │ STT           │ TTS                             │ store the phone
                                    ┌──────▼─────┐   ┌──────▼──────┐              Claude Code │ Remote-Controls
                                    │ Whisper    │   │  Kokoro     │              CLI sessions│ (no cross-surface
                                    │ (faster-   │   │  (local TTS)│              live here   ▼ sync — this hits
                                    │  whisper)  │   └─────────────┘                          the REAL session)
                                    └────────────┘
```

***Component placement***

| Component | Runs on | Notes |
|---|---|---|
| **Voice bot** (discord.js) | **SundaeStackarr**. Always-on Docker container, sibling of `claudediscordbot`. Must be always-on to answer the instant the owner joins. | BigHonker is rejected as a primary host because it enters sleep mode after 45 minutes of idle time. It remains available only for Whisper GPU offloading.
| **Whisper (STT)** | Bot host CPU or Softserve (`faster-whisper`, CUDA). |
| **Kokoro (TTS)** | Bot host. Small model footprint suitable for light workloads and sufficient performance on the CPU alone. Uses Apache-2.0 licensing.
| **claude-bridge shim** | **Softserve**. HTTP service wrapping `claude -p --resume`. Serves as the "swappable brain" seam for HAI integration, co-located with Claude Code CLI and Remote Control.

---

## 3. Key decisions & constraints (settled)

- **Subscription, not API.** The brain is the Claude Code CLI on Softserve, driven headless (`claude -p --resume`). This configuration bills against a subscription rather than metered usage.
- **Voice adds NO extra Claude cost** — spoken turns consume resources identical to typed conversational turns. Speech-to-text (Whisper) and text-to-speech (Kokoro) are local services; they never invoke the API. Only conversation turns count toward the weekly cap.
- **Sessions do NOT sync across surfaces.** The phone/web Claude Code app maintains a separate session store from the local CLI.
  - *Constraint:* One active driver per session at any time.
  - *Implication:* Do not voice-drive a session while another application (e.g., the mobile app) is live-attached to that same session. Access must be via Remote Control bridging to the CLI session on Softserve.
- **Discord as transport.** Discord applications provide reliable background microphone access and full-duplex audio for barge-in over Bluetooth. This bypasses iOS Safari PWA mic-suspension limitations affecting web transports.
  - *Architecture:* Audio transits Discord servers; STT, TTS, and the brain remain local.
  - *Fallback:* A fully-local SIP/webpage transport is available if transport privacy becomes a higher priority than this convenience.
- **Build as a discord.js sibling of the jukebox.** `claudediscordbot` extends `lidarr-music-bot v9`. The stack utilizes:
  - `discord.js v14`
  - `@discordjs/voice` (includes `@discordjs/opus`, `sodium-native`, `prism-media`, and is compatible with external `ffmpeg-static`).
- **Package utility:** A single instance of the `@discordjs/voice` package handles both playback and STT-in via a custom `VoiceReceiver`. The discord.js platform provides superior real-time receive capabilities compared to alternative paths (e.g., `discord.py`).
- **Wake word mechanism.** Presence in the Discord voice channel signifies an active listening state; leaving implies completion. An optional push-to-talk setting may be used as a manual turn delimiter.
  - *Note:* No Porcupine wake-word engine is required for this transport.

## 4. Phase plan (each phase ships something demonstrable and de-risks the next)

**Goal:** Remove every unknown before writing code by establishing definitive host, service identity, and environment constraints.
1. **Host Decision — SundaeStackarr.** The bot operates as an always-on container (sibling of the jukebox) on a Docker host that supports reliable 24/7 uptime. An alternative host is excluded due to sleep policies incompatible with continuous connection requirements for voice gateways and Discord presence.
2. **Service Identity — Developer Portal Configuration:**
The following intents are required:
   *   Server Members
   *   Voice State
Intents explicitly avoided: Privileged Message Content (mitigated by using slash commands solely for text interactions). Public Bot mode is OFF; the bot acts as an owner-only service.
3. **Guild Topology.** A private guild configured with one voice channel and one text channel. Access to these channels is restricted exclusively via Lock permissions tied to a specific user ID.
4. **Headless Execution Model:** The assistant head must run non-interactively (`claude -p --resume <id>`) on the primary login environment, returning clean stream-json output. Permission posture for unattended turns follows Phase 1 definitions.
5. **Model Selection Constraints:**
   *   Speech-to-Text (STT): `faster-whisper`. CPU variant defaults to architecture A; GPU variants utilize architectures B or C when available.
   *   Text-to-Speech (TTS): Kokoro voice family (selection from 54 supported options).
6. **Secrets Management:** Authentication tokens and SSH keys reside in a dedicated secrets file within the container environment path, never hardcoded in source code.

### Voice Interface: Headless Claude Bridge

#### Design Doctrine
The voice interface uses a headless bridge to interact with Claude. The bot communicates only via HTTP; it never invokes the CLI directly. This abstraction isolates the voice session from the operator’s interactive workflow.

#### Permission Model
Permission mode is a **per-invocation flag**. It is NOT stored in the session state. This ensures independence between headless voice turns and the owner’s interactive sessions, even when sharing the same conversation ID.

*   **(a) Conversational-only:** Answers, reasoning, planning, reading. Changes are queued for the operator’s next desk session.
*   **(b) Auto-for-safe + spoken confirm:** *Selected default.* Runs read-only/safe actions automatically. For destructive actions, it asks for spoken confirmation and waits for affirmative input.
*   **(c) Full-auto (`bypassPermissions`):** Executes all actions unattended.

**Standing Posture:** Mode (b). Implementation must document the exact flags/allowlist for this mode. Initial test sessions may start more conservatively to build trust, but the standing design is (b).

#### API Specification
The bridge runs as a small HTTP service on Softserve (Node or Python), bound to LAN/Tailscale only.

*   **Authentication:** Shared token required.
*   **Endpoint:** `POST /ask`
    *   **Body:** `{"session_id": "<optional>", "text": "<string>"}`
    *   **Logic:**
        1.  If `session_id` is present: Resume session.
        2.  If `session_id` is absent: Start fresh; capture and return the new ID.
        3.  Execute `claude -p "<text>" --resume <session_id> --output-format stream-json`.
        4.  Parse final assistant message text.
    *   **Response:** `{"session_id": "<string>", "reply": "<string>"}`
*   **Session Management:** A small store maps conversation identifiers to session IDs. The bot passes the ID in every request. A `/sessions` list endpoint is reserved for Phase 6 (picker).

#### Acceptance Criteria
1.  `curl -XPOST /ask -d '{"text":"hi"}'` returns Claude’s text.
2.  A second call with the returned `session_id` resumes the same conversation.
3.  Confirmed usage of Max sub (expected behavior).
4.  STT/TTS pipelines remain untouched.

## Phase 2 — Text-only Discord bot (prove Discord ↔ brain)
The routing loop operates without audio complexity.

1. **Containerization:** Deploy `discord.js` as a jukebox-sibling service/container on the chosen host.
   
2. **Ingestion and Routing:**
   - A slash command (`/ask <text>`) is available for user invocation.
   - Alternatively, listening occurs in a dedicated text channel to forward input.
   - Forward all ingested text to `claude-bridge /ask`.
   
3. **Response Handling:** Post the reply back into the originating channel or thread.

4. **Session Mapping:** Map one Discord channel/thread strictly to one Claude session id; persist this mapping per message.

5. **Conformance Criteria (Done When):** The system allows the owner to type in Discord and receive a Claude answer on his sub, with state resuming across multiple messages.

### TTS Output Configuration

The voice interface uses Kokoro as the local TTS engine. Kokoro runs as a local HTTP endpoint on the bot host, exposing an OpenAI-compatible API.

**Audio Playback Flow:**
1. The bot joins the Discord voice channel using `@discordjs/voice`.
2. Claude’s text reply is sent to the Kokoro endpoint.
3. The resulting audio is played into the voice channel.

**Operational Mode:** Half-duplex (text input, voice output).

**Streaming Optimization:** The system supports streaming playback, where audio for initial sentences begins playing while subsequent sentences are still being generated by Kokoro.

### Phase 4 — STT-in (talk to Claude)

**Goal:** Close the loop with full spoken conversation.

1. Add `@discordjs/voice` **`VoiceReceiver`**; subscribe **only to the owner's user id** (avoids echo/other speakers).
2. VAD / silence detection segments an utterance; decode Opus → PCM → **Whisper** (GPU offload with CPU fallback).
3. Pipe transcript → `claude-bridge` → Kokoro → playback.

**Design Fact:** The system closes the loop when the owner joins a voice channel, speaks, and hears Claude's spoken answer end-to-end at the desk.

### Phase 5 — Barge-in + turn-taking polish

#### Design doctrine for interruptible conversation
The Voice Interface ensures natural, interruptible interaction by terminating system playback immediately upon detecting user speech during active Kokoro output. The interface monitors the receive stream via VAD to identify when the owner speaks.

**Operational behavior:**
The following conditions govern turn management:
1. **Playback Interruption**: If Niko begins speaking (detected via VAD on his receive stream) while Kokoro is playing, playback stops immediately and a new generation turn initiates.
2. **Timing Calibration**: End-of-utterance timing is tuned to prevent cutting off the user or introducing excessive latency.
3. **Transcription Integrity**: The system guards against transcribing its own TTS output by relying exclusively on the owner's receive stream, with continuous verification of this isolation mechanism.
4. **Fallback Delimiter**: A push-to-talk input (Discord PTT) is available as a robust fallback delimiter for noisy environments such as car audio.

## 6. Prior art to crib from
- Architecture reference: mbailey/voicemode (local Whisper+Kokoro).
- Discord voice-assistant patterns:
    - Streaming Whisper sink implementations
        .
        KickerMix/Discord-Local-LLM-VoiceChat-Bot, pladisdev/Discord-AI-With-STT.
        VocalCord by widavies.
    - Voice receiver helpers and documentation
        - @discordjs/voice `VoiceReceiver`
        .
        Rei-x/discord-speech-recognition (receive helper).
- Audio OUT playback code:
    claudediscordbot (`/opt/arrstack/claudediscordbot/index.js`), verified as proven.

## 9. System State and Deployment Notes

The Voice Interface is deployed as an integrated module within the JukeBot process (`claudediscordbot`) rather than as a standalone service. This design reuses the existing Discord token and gateway connection to avoid credential friction. The standalone `claude-voice-bot/` code resides in the mirror as a reference for potential future separation (see §8 Seam Design).

### Component Architecture

**JukeBot Integration (`claudediscordbot`)**
The voice assistant logic resides in `/opt/arrstack/claudediscordbot/voice-assistant.js`. It attaches a `voiceStateUpdate` listener to the main client. Activation is gated by user ID (the owner) and channel ID (`config.voice.assistantChannelId`). When triggered, the bot joins the specified channel and initiates the audio pipeline: VoiceReceiver → Whisper → Bridge → Kokoro → Playback. Barge-in functionality stops playback when the authorized user speaks. No slash commands are registered for the assistant to prevent conflict with existing JukeBot commands.

Initialization is handled via `initVoiceAssistant(client, config)` in `index.js`, called before `client.login`. Configuration is stored in `config.json` under the `voice` block, including `enabled`, `allowedUserId`, `bridgeToken`, and `assistantChannelId`.

**Claude Bridge (`claude-bridge/`)**
Hosted on Softserve (Node), port 8790. Wraps `claude -p --resume --output-format json`. Prompt input via STDIN prevents shell injection. Implements conservative allow-list permissions and handles OAuth token expiration (401/re-login). Endpoints include `POST /ask` and `GET /health`. Includes a session picker interface (`/sessions`, per-session `cwd`) to enable cross-project resume. The bridge sanitizes the child environment by stripping `CLAUDE_CODE_*` and `ANTHROPIC_BASE_URL` variables.

**Audio Services**
- **Whisper**: Hosted on SundaeStackarr, port 8000 (`faster-whisper`). Verifies functionality via `/v1/models` (HTTP 200).
- **Kokoro**: Hosted on SundaeStackarr, port 8880. Provides 68 voices for TTS.

Both services are integrated into the live `docker compose` stack alongside the bot and Whisper components. Dockerfile, package.json, and deployment snippets are maintained in the source tree.

### Operational Status

The system is LIVE. End-to-end testing confirms:
1.  CLI authentication is resolved; `.credentials.json` holds valid subscription and refresh tokens.
2.  Bridge responds to `/ask` with valid Claude answers; reachable from Sundae `arrnet` (HTTP 200, ~4ms latency).
3.  Audio pipeline functions: Whisper transcribes silence-segmented utterances; Kokoro generates audio/mpeg playback.
4.  JukeBot rebuilds without error; `[Voice-AI] armed` status reported.

### Pending Configuration

- **Durable Autostart for Bridge**: The bridge is currently a session-tied process on Softserve. It requires an autostart entry to survive host reboots. Current recommended mechanism: on-logon scheduled task. Implementation pending operator approval; unapproved persistence mechanisms (e.g., hidden VBS tasks) are prohibited.
- **Parameter Tuning**: `silenceMs` and barge-in sensitivity require live tuning after initial usage.
