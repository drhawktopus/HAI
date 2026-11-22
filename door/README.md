# The door (Uhura)

Uhura is HAI's door for hosted assistants such as ChatGPT and Claude: an MCP server on a public address that lets an
assistant you have signed in read the documents you publish to it, read the transcripts you publish to it, keep notes
on its own desk, and send requests to the head through one fixed account. It cannot reach anything else.

## The parts

| Part | Where it runs | What it does |
|---|---|---|
| `netguard` | the door's machine | Owns the door's network and refuses new connections to private ranges. It alone holds `NET_ADMIN`. |
| `uhura` | the door's machine, inside netguard's network | The MCP server. Google sign-in for the owner's account; tools for documents, transcripts and the desk. |
| `liaison/` | the head (`examples/full-stack`, `--profile door`) | Carries the door's requests to the head under one account, with a shared secret. |
| `deploy/publish_library.py` | a timer beside the library | Publishes the library to the door's `published/` folder, sanitized: what your private rules name is withheld. |
| `deploy/publish_transcripts.py` | a task on the machine with the transcripts | Publishes filtered transcripts to `UHURA_TRANSCRIPTS_DIR`. |
| `deploy/desk_sync.py` | a timer | Copies the desk into the lab (`CHATGPT_HOME`) from outside the door's container, so the internet-facing half never writes to the library. |

## Setting it up

1. On the head, start the liaison: put `UHURA_LIAISON_SECRET=<random value>` in `examples/full-stack/door.env`, then
   `docker compose --profile door up -d uhura-liaison`.
2. On the door's machine, in this folder: copy `.env.example` to `.env` and fill it in (it holds secrets: mode 600).
   Then `docker compose up -d --build`.
3. Give the door a public name: `deploy/Caddyfile.block` is a Caddy template (`{UHURA_HOSTNAME}` is the public name,
   `{UHURA_UPSTREAM}` the address and port the proxy reaches Uhura at, `8478` by default). Tools, tokens and
   registration answer only the assistant vendors' published address ranges; `deploy/uhura_ranges.py` refreshes the
   list the template imports.
4. Write your private rules: copy `deploy/private_rules.example.json` to `deploy/private_rules.json` (git-ignored) and
   list the names and topics that must never be published. The publisher refuses to run without the file;
   `UHURA_PRIVATE_RULES=none` says on purpose that you have none.
5. Install the library publisher and the desk: `/etc/default/uhura` names the folders (`UHURA_HOME`, `UHURA_USER`,
   `UHURA_LIBRARY`, `UHURA_CODE_ROOT`, `UHURA_PRIVATE_RULES`, `CHATGPT_HOME`); then run `deploy/install_publisher.sh`
   and `deploy/install_desk.sh` as a user with passwordless sudo (they install systemd units).
6. Publish transcripts, if you want the door to read them: on the machine that has them,
   `deploy/install_transcript_task.ps1 -OutDir <the folder the door mounts as UHURA_TRANSCRIPTS_DIR>`.

## Updating and testing it

`deploy/uhura.env.example` lists the settings of the machine that ships and tests the door (copy it to
`deploy/uhura.env`). Then:

- `bash deploy/uhura_gate.sh` runs every test in a throwaway container with no route out, and fails on a short count.
- `bash deploy/ship_uhura.sh` runs the gate, ships the committed files, rebuilds and restarts Uhura, then runs the
  live attacks.
- `python deploy/attack.py` attacks the running door through its public address and directly, and exits non-zero on
  any failure: requests with no token or forged identity headers, client-supplied URLs in sign-in, outbound reach to
  the LAN (with a control that the internet is still reachable), a root container, the off switch, and the signing key
  appearing in logs.
