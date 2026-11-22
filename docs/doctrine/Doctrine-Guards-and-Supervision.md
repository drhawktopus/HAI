# Doctrine — Guards & Supervision

Project-agnostic operating doctrine for building guards, watchdogs, patrols, and supervisors. These
are declarative laws; the dated case-law behind each lives in the lab's private working notes and the work
journals. For how to *work* in the lab, see [[OPERATING-BRIEF]]. Sister pages:
[[Doctrine-State-and-Artifacts]], [[Doctrine-Measurement-and-Verification]],
[[Doctrine-Build-and-Migration-Discipline]].

## A dead reporter is not a dead worker; a live heartbeat is not a live worker

"Could not measure" must never render as any definite state — a guard that cannot measure says so
loudly (SILENT / UNKNOWN / INCONCLUSIVE), never "healthy" and never "down." Equally, a fresh
heartbeat does not prove a live worker: a push or heartbeat thread can outlive the work thread it
vouches for. Liveness derives from the worker's OWN beat, and its truth is the age of a fresh
heartbeat FILE — not "the process exists," not "a push arrived," not task status. The reporter
declares its own cadence; the verdict is derived from that cadence, not a flat constant.

## Supervision is factory machinery — a session may subscribe, never supervise

A loop that watches factory work AND can act on it (stop, resubmit, kill) must run ON-BOX — a
scheduled task with repetition and IgnoreNew, or the on-box daemon — so it cannot die with a chat
session or race the factory's own machinery. A session-side watcher may only READ and EXIT ON A
VERDICT; it subscribes to signals the factory already publishes and acts on the wake. It never
mutates the floor.

## A diagnosed hole in a guard is fixed the same pass

A known blind spot in a safety guard is never backlog food — deferral converts it directly into a
future incident, on a schedule the hardware picks. Fix it in the same pass. If a real decision blocks
part of it, build the parts that need no decision now and put the decision in front of the operator that day.

## Baselines come from the first value actually observed

Take a guard's baseline or threshold from the first value it actually observes — never a sentinel,
never a bench prediction. A falsy default silently disables the check at exactly the boundary it
exists to catch. Calibrate a threshold in the regime it will be used in; if the library publishes the
band, use the published band. A guard may not narrate a state it did not measure.

## Detection and action share one derivation

The signal a guard acts on and the signal it reports must come from one derivation. Two
implementations of one idea drift into disagreement — a process can become too unlike a target to be
a *candidate* yet still be judged *chargeable*. Use one shared constant or function, with a test
asserting the two agree.

## A transient condition never reaches a work-consuming catch-all

Type the fates of a work item: WAIT (a resource is busy) · ARCHIVE (the input is broken) ·
HOLD-FOR-HUMAN (the machinery is misbehaving). Only a property of the work item itself may consume the
work item. A transient (a busy lane, an unstoppable worker) that reaches a generic "on any exception,
archive" handler destroys healthy work. Raise a distinct typed exception, caught before the
catch-all.

## Cleanup belongs to the caller that sees the end by any route

Put cleanup (clear the claim, retire the staged artifact) in the caller that sees the work end by ANY
route — cleanup inside the happy path is skipped by every exception. And nothing in a cleanup or
`finally` path may raise: an exception thrown while cleaning up replaces the exception in flight and
destroys the diagnosis it exists to protect.

## A retry must change something that matters

A retry that does not change the condition that caused the failure is the same attempt run twice. Ask
what the retry changes; if the answer is "nothing relevant to this failure," it is not a retry.
Supervision asks the worker its status (TCP, not a stopwatch), fails closed when it cannot ask, and
kills only on demonstrated idleness — a duration cannot supervise work whose cost you do not control.

## Guards never police their own bookkeeping, the operator's controls, or what they did not measure

A guard may not flag its own ledger, the documented operator stand-down or pause controls, or any
state it did not actually measure — and a guard that can crash or kill its host is worse than no
guard. A guard's error handler must not be able to reproduce its own failure (for example, re-emitting
the character that raised inside the handler).

## A destructive guard needs a higher bar to act than to report

Split the beat: a broad signal → REPORT; a narrow, high-confidence signal → ACT. Scale severity with
whether there was anything to protect — an idle card downgrades an arrest to a report. Give the
operator a documented stand-down flag and never police that flag. "Already gone" is not "resisted": a
tree-kill takes the children, so each child's own kill then returns not-found — do not count those as
escapes.

## A guard is not verified until a live decoy proves it

A green unit suite is not evidence a detector works when its failure mode is "silently finds nothing"
— that bug lives in the SEAM between producer and consumer, where unit tests do not look. Share one
constant between emitter and parser, assert they match, and prove the whole guard end-to-end with a
live decoy: fire the real signal and watch it catch. Validate the instrument against a known positive
before trusting a negative.

## An exemption covers the process tree, not the process you looked at

An exemption must cover a process's children — child-of-exempt-is-exempt, bounded depth, cycle-safe.
Ask "what actually does the work, and what spawns it?", not "what name is in the alert." Keep the
inverse honest: an orphaned instance with no exempt ancestor is still in scope, so the rule never
silently becomes "never touch this binary."

## Exclude the observer

A `CommandLine` or `-f` filter matches the querying process's own command line, so "is X running?"
finds the query asking the question, and `pkill -f x` kills itself. Write the exclusion FIRST, before
reading the result: filter by process NAME not command line, use the `[x]pattern` bracket trick, or
compare against `os.getpid()`.

## The candidacy list is the ceiling

Whatever the first, broad filter misses, no later tier can ever act on. Union the act-list into the
candidacy-list and test the narrow tier end-to-end from enumeration. Match on what a process IS
(executable plus script entrypoint, basename-equal), never on its arguments or output paths; only
binary or directory signatures may be substring-matched, and a retyped invocation is an idealised one
— pin the real bytes from the guard's own log.
