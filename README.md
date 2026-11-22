# HAI

HAI is the home AI I run on my own hardware. It is one assistant that my household and friends talk to, and behind it
is a lab: a head that takes the conversation and decides where the work goes, and a set of foundries (my gaming PC, my
desktop, the house server, an old office PC and a cluster of tablets) that do the work. None of these machines were
bought for AI. The write-up of what it does and how it performs is in
[home-ai](https://github.com/drhawktopus/home-ai) and on [my portfolio](https://portfolio.hawktopus.org).

This repository is the code. It is the working code of a lab that is running right now, published as it is. Every
network address, model choice and machine-specific path the code reads is a setting; the names of my machines and lanes
remain as role names, and my own tables (tested contexts, bench standards) ship as example data a stranger edits.
With no device roster the head is one machine holding every role; a lab with several machines lists them in a roster
file, and my own roster ships as a worked example (names and cards, no addresses). An installer sets up a machine as a
head, a foundry, both, or a tablet head (`foundry/setup_factory.py --role`). Next, machines will register themselves
instead of you writing the roster. That work happens here, in the open.

## What is in here

| Folder | What it is |
|---|---|
| `head/` | The orchestrator: the chat page, the agent loop and its tools, the order desk, placement across machines, the library it reads, the per-user design (profiles, admins, per-user memory and documents), consent checks, self-engineering under human gates, and its test suite (about 4,000 tests) |
| `foundry/` | The factory every GPU machine runs: it takes orders, claims its card, serves models, trains and renders, reports its own state and abilities, and works on its own until it is linked to a head; `lab.env.example` lists its settings |
| `tablet-foundry/` | The same idea for a cluster of old Android tablets and an old office PC |
| `engine/` | The model server the factories run: upstream llama.cpp with the lab's patches, so a run that stops anywhere continues token for token; the scripts that apply them and build it for each kind of card, and the orders that prove a new build before it serves; its README lists every patch |
| `fellowship/` | Building and keeping those tablets: flashing, provisioning, the roster, their USB network, the on-tablet WiFi watchdog and the battery governor; its README gives the setup in order |
| `door/` | A small MCP server that lets my ChatGPT and Claude accounts read my lab's library; its README gives the setup in order |
| `tools/` | The lab's shared programs: deploy doors, health and load reporters, the update system, probes |
| `house/` | Tools for the rest of the house: a Plex monitor and a Discord relay |
| `addons/media/` | An optional add-on that lets HAI work with a home media stack you already run; it has its own README |
| `examples/` | Compose files to start from: `one-machine/` (the head alone), `full-stack/` (the head with everything it runs beside it), `tablet-head/` (Tolkien) |
| `etc/` | `lab.env.example`: every setting the lab tools and the fellowship tools read |
| `docs/` | The design behind it: the quick-start, measuring your own hardware, orders, the factory contract, the lab's operating rules, and the lab's own library of runbooks, reference manuals and design documents (addresses are placeholders for your own) |

The priced menu the placement reads ships in `menu/`; the full benchmark results are in
[home-ai](https://github.com/drhawktopus/home-ai) and [fellowship-gauntlet-results](https://github.com/drhawktopus/fellowship-gauntlet-results).
The research behind it ships too. [docs/tuning-guide.md](docs/tuning-guide.md) turns it into good starting configs
for any model on any card: what fits where, the thinking and temperature settings each job wants, the arsenal of
speed levers and what each one measured, and how to measure your own. A card like one of the lab's can start from the
lab's measured rows on its first turn.

## What you bring

- **Your models.** HAI runs them through [Ollama](https://ollama.com) on your own machines; the example uses one Ollama.
- **Your people.** Profiles, admins and the owner are settings; the code and its tests use example names.
- **Your secrets.** Every credential is a file your settings name (`NAME_FILE=/path`); the repository holds none.
- **Your documents.** The library HAI reads is a folder of Markdown you fill.

## Set it up

You need Docker with Compose, and an [Ollama](https://ollama.com) server the head can reach, on the same machine or
another one on your network. These steps run the head on one machine with every role pointed at that one Ollama. The
installer can do steps 3 and 4 for you (`foundry/setup_factory.py --role head`; [docs/quick-start.md](docs/quick-start.md)
shows the command).

1. Pull a chat model and the embedding model into Ollama:

   ```
   ollama pull qwen3:8b
   ollama pull nomic-embed-text
   ```

   Ollama listens on its own machine only by default. So the head's container can reach it, set `OLLAMA_HOST=0.0.0.0`
   where Ollama runs and restart Ollama (Ollama's FAQ shows how on each operating system). That opens Ollama, which has
   no sign-in, to your network too: on a network you do not fully trust, firewall port 11434
   ([docs/quick-start.md](docs/quick-start.md), step 2).
2. Get the code and go to the one-machine example:

   ```
   git clone https://github.com/drhawktopus/HAI
   cd HAI/examples/one-machine
   ```

3. Copy the example settings and make them yours:

   ```
   cp settings.env.example settings.env
   python -c "import pathlib; [pathlib.Path(n).mkdir(exist_ok=True) for n in ('library', 'state', 'usercorpora')]"
   ```

   The second line makes the head's three data folders (on Linux, Docker would make a missing one as root). In
   `settings.env`, replace the example users (`PROFILES`, `ADMIN_USERS`, `OWNER_USER`) with your own names. The
   example already points at an Ollama on the same machine (`http://host.docker.internal:11434`, which the compose file
   maps on Linux too) and names the two models above. With Ollama on another machine, set `BRAIN_HOST` and
   `EMBED_HOSTS` to its address, `http://<that machine's address>:11434`. If you pulled a different chat model, set `BRAIN_MODEL`,
   `FAILOVER_MODEL` and `HEAVY_MODEL` to its name.
4. Start the head:

   ```
   docker compose up -d --build
   ```

5. Open `http://localhost:8099`, pick your name, and ask something. The example keeps the head to this machine: it
   trusts the name a person picks, so opening it to your network is a choice to make on purpose
   ([docs/quick-start.md](docs/quick-start.md), "Who can reach it").

[docs/quick-start.md](docs/quick-start.md) has each step in more detail: how to check the head can see Ollama, asking
from the command line, the context-window settings, and keeping the port to this machine. It also sets up the head's
factory, the process beside the card that serves and measures models, renders and trains (Windows-first today).
I ran that example from this repository's own files, with only the example settings and its own Ollama, and it
answered. Self-registration is not built yet: for more than one machine, the installer sets up each one, and you write
the roster for your machines by hand.

## Running the tests

From a checkout of `head/`, run `python tests/run_suite.py`. It points every store it writes at a temp folder, gives
the suite example users, and makes no connection off the machine (its guard allows loopback only; Python's own bytecode
caches land in the checkout, as always).

## License

PolyForm Noncommercial 1.0.0 (`LICENSE.md`): use for any noncommercial purpose is welcome, personal, hobby, research and
educational included; commercial use is not. The licence text is the terms. See `NOTICE`.

## How it was built

I designed and direct this lab. Most of the code was written by AI coding agents working under my direction, mostly
Claude, and checked against the lab before it went live. The rules they work under are written up on
[my portfolio](https://portfolio.hawktopus.org/doctrine).
