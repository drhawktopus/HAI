# The factory contract

A factory is the program each worker machine runs (`foundry/`, and `tablet-foundry/` for a cluster of small CPU
devices). Every factory runs the same files. What differs from one machine to the next is settings, never code: the
factory's root folder (`FOUNDRY_ROOT`), its name in the fleet, the head it is linked to (`HAI_URL`), and the
addresses in its own `etc\lab.env`. A second implementation would drift from the first; one set of files cannot.

## What every factory does

These are the parts that must never differ between factories:

- It accepts an **order**: a spec file in its queue folder, from a person or from the head.
- It **claims the lane**, so the fleet knows that card is busy.
- It **publishes a status card and a heartbeat** on a fixed cadence.
- It **resumes idempotently**: an interrupted order picks up from what it already finished.
- It honours **pause** and **priority**, sent as control slips.
- It **retires** the order and releases its claim by any exit path, including a crash.
- It **counts failures**. A failure is never written down as a success.
- It patrols its own card, and **holds** an order whose requirements it cannot meet.

What varies by machine: which kinds of work it runs, which guards apply, and the thresholds they use.

## Capabilities are a promise

Each factory declares what it can do (`FACTORY_CAPABILITIES`). A capability is listed only when the code behind it is
deployed on that machine, so the list is a promise, not a wish. An order that needs a capability the factory does not
have is **held**, with the missing capability named. It is not failed and not thrown away, and that is what makes
"build the capability, then the order runs" a real workflow.

A missing capability is a fact about what is deployed, not a verdict on the hardware. A card that does not render
today may render next month, and the factory detects that from what is installed rather than from its name.

## A factory works alone

With no head address set, a factory works from its own queue, pushes nothing, and says so once in its log. Linking it
to a head is one setting (`HAI_URL`) and a worker token. The head then places work on it and reads its status; the
factory stays the one that fulfils the order.

## The lesson that shaped it: single-writer stores

Several status files in the lab were written by exactly one process for their whole lives, so none of them needed a
machine name in it. All of them stopped being single-writer the moment a second factory existed, and none of them
raised an error: one factory's status quietly overwrote another's. The rule that came out of it: **when you add a second
producer, audit what the first one writes, not just whether its code is portable.** Every per-machine store carries
the machine's name.
