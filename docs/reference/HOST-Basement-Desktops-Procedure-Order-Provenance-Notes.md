# Basement Desktops — Procedure Order, Provenance & Notes



## Procedure Order

Onboard hosts under their existing hostnames until §C completes. Name assignment is gated by capacity measurement.

1. **§A** Physical setup and OS installation (requires local access).
2. **§B** Remote access provisioning (requires SSH).
3. **§C** Capacity measurement and tier verdict.
4. **§E** Final rename migration.
5. **§D** Open questions (if requirements are missing from the library).

See [[HOST-ONBOARDING-Basement-Desktops]] for steps §A–§E.

## Provenance

The following sources define the operational facts and design doctrine for this host. The newest correction in any source takes precedence.

- [[RUNBOOK]] §§ELEVATION, "Workgroup / domain", "GPU host power configuration", "SMB from Mothership"
- [[P20-MULTI-FACTORY-FLEET]] §§1–4, 7
- [[Fellowship-Crunch-Integration]] §§2, 4, 7, 10
- [[Network-Infrastructure]] (DNS roster, Pi-hole `hosts/`, share letters)
- `CLAUDE.md` doctrine table (co-location probe, one-variable, n=1, single-writer, rank-based slicing, deployed≠running, guards need restart policies)
