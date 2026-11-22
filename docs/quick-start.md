# Quick start: the head on one machine

This gets HAI's head running on one machine and answering, with one Ollama server behind it. It is the path that was
run end to end from this repository's own files, with nothing else configured.

## What you need

- Docker with Compose.
- Git, and Python 3 (the lab runs 3.12) for the installer and the factory; the head itself runs in its container.
- An Ollama server the head can reach: on the same machine, or another one on your network.
- A chat model and the embedding model pulled into it, for example:

  ```
  ollama pull qwen3:8b
  ollama pull nomic-embed-text
  ```

  Any chat model works for a first answer; pick the one your card holds fully. [tuning-guide.md](tuning-guide.md)
  gives good starting configs for any model on any card, from the lab's research, and
  [measure-your-own-box.md](measure-your-own-box.md) checks them on yours.

## Steps

The installer can take steps 1 to 4 for you once you have the code. It says what this device is with `--role`:
`head`, `head+factory` (a head that is also a factory, as the lab's own head machine is), `factory`, or `tablet-head`.
Get the code, then run it from the repository's top folder without `--apply` first: it lists every step it would take
and changes nothing. Put your own name in all three places (you are the only profile, an admin, and the owner):

```
git clone https://github.com/drhawktopus/HAI
cd HAI
python foundry/setup_factory.py --role head --head-set BRAIN_HOST=http://host.docker.internal:11434 --head-set PROFILES=<your name> --head-set ADMIN_USERS=<your name> --head-set OWNER_USER=<your name>
python foundry/setup_factory.py --role head --head-set BRAIN_HOST=http://host.docker.internal:11434 --head-set PROFILES=<your name> --head-set ADMIN_USERS=<your name> --head-set OWNER_USER=<your name> --apply
```

Then open `http://localhost:8099` (step 5). By hand, the same steps are:

1. Get the code, go to the one-machine example, copy its example settings, and make the three folders the head keeps
   its data in:

   ```
   git clone https://github.com/drhawktopus/HAI
   cd HAI/examples/one-machine
   cp settings.env.example settings.env
   python -c "import pathlib; [pathlib.Path(n).mkdir(exist_ok=True) for n in ('library', 'state', 'usercorpora')]"
   ```

   Make the folders yourself before the first start: on Linux, Docker makes a missing one as root, and you could then
   not add the factory's token to it ("Give the head its factory").

2. In `settings.env`, point the host settings at your Ollama server (`BRAIN_HOST` and `EMBED_HOSTS`; with no device
   roster, `FAILOVER_HOST` and `HEAVY_HOST` follow `BRAIN_HOST` when you leave them out). The head runs in a container,
   so the address is the one the container sees:
   - Ollama on the same machine: `http://host.docker.internal:11434`. Docker Desktop knows that name; on Linux the
     example's compose file maps it to the machine (its `extra_hosts` line).
   - Ollama on another machine: its address, for example `http://<its-ip>:11434`.
   - Ollama in another container of the same compose project: its service name, for example `http://ollama:11434`.

   Ollama listens on its own machine only by default. For a container to reach it, set `OLLAMA_HOST=0.0.0.0` where
   Ollama runs (Ollama's FAQ shows how for each operating system) and restart it. That opens Ollama to your network as
   well, and Ollama has no sign-in of its own: anyone who can reach port 11434 can use its models, pull new ones and
   delete them. On a network you do not fully trust, let your firewall admit port 11434 only from the head's containers
   and your factories, and never forward it from your router to the internet. After the head starts (step 4), check
   that it can see the server; this prints Ollama's version, and an error here means the address is wrong:

   ```
   docker compose exec hai python -c "import urllib.request, os; print(urllib.request.urlopen(os.environ['BRAIN_HOST'] + '/api/version').read())"
   ```
3. Set the model names to the models you pulled (`BRAIN_MODEL`, `FAILOVER_MODEL`, `HEAVY_MODEL` and `PC1_BRAIN_MODEL`
   for the chat model, `EMBED_MODEL` for the embedding model), and replace the example `PROFILES`, `ADMIN_USERS` and
   `OWNER_USER` with your own names (the first name in `PROFILES` is you). Admins may change settings; the owner is the one HAI names when something needs approving. The
   example sets every context window to a small starting size (`NUM_CTX` and its per-role forms); it is a starting point,
   not a measurement, so set yours once you have measured your card ([measure-your-own-box.md](measure-your-own-box.md)).
   If your card resembles one of the lab's, two commented lines in the settings start you from that card's measured
   rows instead ([measure-your-own-box.md](measure-your-own-box.md), "Start from the lab's measurements").
   The head trusts the name a person picks, so the example keeps it to this machine; "Who can reach it" below says how
   to open it to your network, and what that means.
4. Start it:

   ```
   docker compose up -d --build
   ```

5. Open `http://localhost:8099`, pick your name, and ask something. You can also ask from the command line (Bash):

   ```
   curl -N -X POST http://localhost:8099/query/stream -H "Content-Type: application/json" \
        -d '{"text": "In one sentence, what is a home lab?", "user": "<your name>"}'
   ```

   or from PowerShell:

   ```
   curl.exe -N -X POST http://localhost:8099/query/stream -H "Content-Type: application/json" -d '{\"text\": \"In one sentence, what is a home lab?\", \"user\": \"<your name>\"}'
   ```

   The answer streams back, ending in a `result` line that names the model that answered.

## What this setup is, and is not

Every role HAI normally spreads across machines (the everyday brain, the failover and the heavy model) points at the
same Ollama server here. Your documents go in `library/` (HAI reads Markdown under `Reference/`, `LLM-Project/` and
`Runbook/`). What HAI writes lands in three places, all folders of this example: `state/` (chats, memory, run records,
the settings file), `library/` (its own notes under `Notes/` and `Journal/`, and `State/profiles.json`), and
`usercorpora/` (documents and photos people upload, one folder per user).

## Who can reach it

The head has no passwords of its own. On your network a person picks their name, and admins (`ADMIN_USERS`) are names,
so anyone who can reach the head's port can act as any profile, an admin included. The one-machine example therefore
publishes the port on this machine only (`127.0.0.1:8099` in its compose file): you, and a factory on this machine,
reach it at `http://localhost:8099`, and nothing else on your network can.

To use it from other devices, or to link a factory on another machine, open the port to your network: change the
compose file's port line to `"8099:8099"` and run `docker compose up -d` again. Do that only on a network of people you
trust. For anything wider, put the head behind a front door that signs people in and passes the signed-in name on in the
`X-Hai-User` header, removing any copy of that header a client sent: the head then pins every signed-in person who is
not an admin to their own records. A front door protects the head only if it is the only way in: let the head's port be
reached by the front door and your factories alone (a firewall rule, or a port line bound to an address only they
use), and never forward port 8099 from your router to the internet. The full-stack example publishes the port on every
interface, because its factories on other machines report to it, so the same rule applies to it.

**The settings file.** The head reads `settings.env` itself, from its state folder (`state/settings.env`, or the path
in `HAI_SETTINGS_FILE`), before it reads any setting, so it works without Compose too. The format is one `NAME=value`
per line; `#` starts a comment. Anything set in the environment wins over the file, so Compose's `env_file` and the
file never disagree. A line the head cannot read stops it at start, naming the file and the line; a name the head
never reads is listed in its log at start (most likely a typo). Secrets do not go in it: put each one in its own file
and give the head the path as `NAME_FILE` (for example `HA_TOKEN_FILE=/run/secrets/ha_token`).

## Give the head its factory

A HAI head normally has a factory on the same machine: the process that takes orders from the head's desk, claims the
card, serves and measures models, renders and trains, and reports what it is doing. The lab's own head machine runs one
beside its head. The factory is not a container: it runs on the machine itself, next to the card, as a standing service
(a scheduled task on Windows, a systemd unit on Linux), set up by `foundry/setup_factory.py` from the role manifest
`foundry/factory_role.json`.

1. From the repository root (two levels up from `examples/one-machine/`: `cd ../..`; every path in this section is
   relative to it), make the shared token the factory uses when it reports to the head. Any long random string will
   do; it lives in a file on both sides and never on a command line. One Python line writes it, the same in Bash and
   PowerShell (a shell redirect would write the file as UTF-16 in Windows PowerShell 5.1, which the head cannot read):

   ```
   python -c "import pathlib, secrets; p = pathlib.Path('examples/one-machine/state/secrets'); p.mkdir(parents=True, exist_ok=True); (p / 'worker_token').write_text(secrets.token_hex(32), encoding='utf-8')"
   ```

   `settings.env.example` already names it for the head (`P20_WORKER_TOKEN_FILE=/state/secrets/worker_token`, the
   container's view of that folder). The head reads the file when it starts, so restart it after adding the token:
   `docker compose -f examples/one-machine/docker-compose.yml restart hai` (a plain `up -d` leaves an unchanged
   container running with an empty token, and the factory's reports are then refused).
2. Install the factory's Python packages for the Python it will run under: `pip install -r foundry/requirements.txt`.
   The order taker needs only `requests` and `pyyaml`; the rest are for rendering and for dataset orders, and the file
   says which is which.
3. See what the setup would do, then apply it. The factory's `--name` is the lowercase form of the name you gave the
   head as `PC1_NAME` in `settings.env` (`my-pc` for the example's `My-PC`): the head keeps your casing as the label and matches machines by the
   lowercase name, and the factory accepts lowercase letters, digits and dashes only. That is how the head's one
   device and its factory become the same machine. `--shape` is your card's kind, `nvidia` or `amd`:

   ```
   python foundry/setup_factory.py --root <a folder for the factory> --name my-pc --shape nvidia \
          --head http://localhost:8099 --token-file examples/one-machine/state/secrets/worker_token
   python foundry/setup_factory.py --root <the same folder> --name my-pc --shape nvidia \
          --head http://localhost:8099 --token-file examples/one-machine/state/secrets/worker_token --apply
   ```

   On Linux the apply writes units into `/etc/systemd/system` (run it with `sudo`, or pass `--units` for another
   folder). On the `nvidia` shape the order taker, the health daemon, the two GPU reporters and (on Windows) the GPU
   telemetry logger start; the render worker starts only when `--set FOUNDRY_COMFY_LAUNCH=<your ComfyUI launcher>`
   names one. On the `amd` shape the stall rescue (with `--set FOUNDRY_AI_TOOLKIT=<the trainer's folder>`) and the
   stall recorder take the health daemon's place, and on Windows the services that drive the card run as the account
   the machine signs in as (`--user <account>`; ROCm on Windows needs that user's desktop session, so set the machine
   to sign in on its own at boot). The dry run lists each service, who runs it, and what it skips and why.
   The factory is Windows-first today: its singleton and process checks call PowerShell, so on Linux the units install
   and the order taker runs, but those checks are not yet ported; treat the Linux half as unproven until they are.
4. The factory reports its card and its state to the head within a minute. With no `--head`, a factory works alone from
   its own queue folder and reports nothing: that is also how a second machine starts before it is linked.

This section is written from the setup script, its manifest and the way the lab's own factories are linked. It has
not yet been walked by someone outside the lab from these files alone; when it has, this sentence goes.

## More than one machine

A factory on another machine reaches the head over your network, so open the head's port first ("Who can reach it").
With no device roster file the head is ONE device ("This machine") holding every role. A lab with several machines lists
them in a roster file (`HAI_ROSTER_FILE`, by default `state/device-roster.json`: one record per device with its role,
model-server setting, card and memory); the head names every machine from that file and from nowhere else. Start from
`examples/device-roster.example.json` (two machines, no measured or lab values). The lab's own roster is a test
fixture, `head/tests/fixtures/lab_roster.json`: five machines (one 24 GB card, two 8 GB cards, a VM, the Tolkien box)
and eleven ARM tablets. It carries the lab's measured rates and the clock floor the lab chose for one of its own cards,
so copy its shape, not its values. Each machine's address is its own setting, never a roster field. Linking a machine
so that it registers itself is not built yet.

## The rest of the stack

- **Everything the head runs beside it**: `examples/full-stack/` adds the coding runner (HAI's coding jobs and the
  head's test suite), the host-ops broker (the one container with the Docker socket, for restarts and self-deploy),
  wake-on-LAN, web search, the self-deploy canary and the door's liaison. It is the same head with more beside it, so
  it takes the one-machine head's place: its compose file lists the steps, the first of which stops the one-machine
  head and moves its folders (chats, memory, uploads and the worker token) across.
- **The tablets**: `fellowship/README.md` builds them in order (flash, provision, roster, USB network), and
  `examples/tablet-head/` runs Tolkien, their order taker.
- **The door** for hosted assistants: `door/README.md`.
- **The lab tools** (`tools/`, `fellowship/`) read `etc/lab.env`; `etc/lab.env.example` lists every setting.
- **A factory, and the head's deploy doors**, read `<root>\etc\lab.env` beside the factory's own folder;
  `foundry/lab.env.example` lists every setting the foundry reads (setup_factory writes a factory's own).

## Running the tests

From the head's folder (`head/`), with its requirements installed (the image carries the head, not its tests):

```
cd head
pip install -r requirements.txt
python tests/run_suite.py
```

The runner points every store at a temp folder, gives the suite its own example users, and refuses any network
connection off the machine, so it touches nothing of yours. It passes only when every test passes and the count
matches the pinned one in `tests/gate_pin.py`.
