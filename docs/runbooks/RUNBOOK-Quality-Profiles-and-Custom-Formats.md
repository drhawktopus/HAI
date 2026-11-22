# RUNBOOK — Quality Profiles & Custom Formats



## Quality Profiles & Custom Formats (Recyclarr)

Managed by Recyclarr v8 syncing TRaSH guide. Config: `/opt/arrstack/recyclarr/recyclarr.yml`. Runs monthly (1st of month, 6am Chicago) via systemd timer, or manually on demand.

### Quality Profiles

| Profile | Used for | Cutoff | Includes | Excludes |
|---------|----------|--------|----------|---------|
| **4K WEB** | Most content | WEB 2160p | WEB+Bluray 720p/1080p/2160p | Remux, BR-DISK, CAM/TS |
| **1080p WEB** | Bulk/low-priority content | WEB 1080p | WEB+Bluray 720p/1080p | 2160p, Remux, CAM/TS |

"4K WEB" grabs 1080p immediately and auto-upgrades to 4K when available. Once 4K is secured, stops searching.

### Custom Format Scores (applied to both profiles)

| Category | Format | Score | Why |
|----------|--------|-------|-----|
| **Block** | LQ, LQ (Release Title) | -10000 | Low quality releases |
| **Block** | Bad Dual Groups, No-RlsGroup | -10000 | Problematic groups |
| **Block** | BR-DISK | -10000 | Full disc rips, massive files |
| **Block** | DV (w/o HDR fallback) | -10000 | Causes HDR compatibility errors on non-DV clients |
| HDR | DV Boost (DV+HDR10) | +200 | Preferred — DV with safe fallback |
| HDR | HDR10+ Boost | +150 | Wide compatibility |
| HDR | HDR | +75 | Universal |
| Audio | TrueHD Atmos | +50 | Best for primary TV |
| Audio | DD+ Atmos | +40 | |
| Audio | TrueHD, DTS-X | +30 | |
| Audio | DTS-HD MA | +20 | |
| Audio | FLAC, DD+ | +10 | |
| Release Groups | WEB Tier 01 (NTb, FLUX, CMRG…) | +1500 | Top streaming rip groups |
| Release Groups | WEB Tier 02 | +1000 | |
| Release Groups | WEB Tier 03 | +500 | |
| Release Groups | UHD/HD Bluray Tier 01 | +1200 | Top Blu-ray encode groups |
| Release Groups | UHD/HD Bluray Tier 02 | +800 | |

### Manual Recyclarr Sync

```bash
# Preview changes without applying
docker compose --profile tools run --rm recyclarr sync --preview

# Apply
docker compose --profile tools run --rm recyclarr sync
```

---
