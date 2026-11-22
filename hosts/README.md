# hosts: each lab machine's own files

One folder per machine of the lab this release comes from, named for that machine, holding what runs on that box and
nowhere else. It is the running lab as it is: you do not need these folders to run HAI on your own machines.

| Folder | What it holds |
|---|---|
| `<box>/launchers/` | What the box's scheduled tasks start: its order taker, render worker, health daemon and stall rescue (`.cmd` and `.vbs`). Each sets the settings that make the shared foundry code that factory (its root, its fleet name). |
| `bighonker/` | That desktop's one-off setup: its power limit, keep-awake, upgrade and task registration scripts. |
| `softserve/` | The head's machine: its launchers. |
| `mothership/` | The AMD training box: its launchers. |
| `rigby/` | The tablet head's compose file for Tolkien; `ENGINE_HOME` comes from the `.env` beside it there. |

## On your own machines

`foundry/setup_factory.py` writes each factory's launchers for you, from `foundry/factory_role.json`, into
`<root>\etc\services\` (`hai_<service>.cmd`), and registers each as a scheduled task named `HAI Factory <service>`.
`docs/quick-start.md` walks through it. These folders are the lab's equivalents, kept because the lab's tasks predate
the installer and the release ships what runs.

## How the lab deploys them

`foundry/fleet_deploy.py` is the one door for code on a factory. It finds every file under `hosts/<box>/launchers/` by
listing this tree, and the `FLEET_ELSEWHERE` setting (in the foundry's settings, `foundry/lab.env.example` shows its
shape) names the folder each one runs from on its box, so each launcher goes to its own box only.

The launchers are kept byte for byte (`.gitattributes` keeps their CRLF line endings). cmd.exe reads a running batch file
by byte offset, so a launcher that a running cmd.exe holds must not grow before the line it is on.
