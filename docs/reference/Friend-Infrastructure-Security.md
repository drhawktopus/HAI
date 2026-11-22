# Friend Onboarding System - Infrastructure & Security



## Droplet hardening configuration
### Network posture and firewall rules

> ⚠ **SUPERSEDED DESIGN — read the AS-BUILT below. Verified live on the droplet 2026-09-13.**
> The paragraph that follows describes the original Phase-1 *plan* (ports 80/443 closed, LE via
> DNS-01). It was NOT built that way and it contradicts the as-built recorded later in this doc
> (§"TLS and Certificate Management": TLS-ALPN-01, DNS-01 not used). It sat here un-reconciled and
> re-taught the wrong answer to the portfolio pass — class 21 (a superseded note must be
> cross-referenced in the same pass). The two sections are now reconciled here.
>
> **AS-BUILT (single DO droplet `do-pihole`, `ss -tlnp` + `ufw` + `/etc/caddy/Caddyfile`, 2026-09-13):**
> - Caddy `v2.11.4` listens on **80 and 443, both public** (ufw: `80/tcp ALLOW Anywhere # Caddy HTTP (ACME + redirect)`, `443/tcp ALLOW Anywhere`). Port 80 is HTTP→HTTPS redirect **and** ACME.
> - **ACME = Caddy default (HTTP-01 / TLS-ALPN-01). NO DNS-01, no DO-DNS module** — the Caddyfile global block is just `email`, nothing else.
> - **Rate-limiting is NOT built** — no `rate_limit` directive and the module is not compiled into this Caddy binary. (The Phase-1 hardening list below calls for it; it was never added.)
> - `fail2ban` active with **`sshd` + `recidive`** jails (NOT Caddy-specific, despite the hardening note below). `unattended-upgrades` active.
> - `ufw` default **deny incoming**; SSH is `tailscale0`-only. Pi-hole DNS (53) + admin (8080/8443) also run on this same droplet.
> - **Firewall fix — glances closed (verified 2026-09-13):** `glances` (61208/tcp) WAS genuinely PUBLIC (`ufw 61208/tcp ALLOW IN Anywhere`, binds `0.0.0.0`, answered on the public IP). Removed BOTH the public `Anywhere` ACCEPT (v4+v6) AND the `<home-public-address>` allow. glances is now **tailnet-only** (reachable only via the blanket `tailscale0` rule). Verified: public path `<droplet-address>:61208` → HTTP 000 (blocked); tailnet `<droplet-tailscale-address>:61208` → 200.
> - **`9876/tcp` (DDNS IP-echo) remains public BY DESIGN** — a dynamic IP must reach it over the public path to learn its own public-facing IP (over Tailscale it would echo the tailnet address, useless for DDNS). Returns only an IP; low-risk. Left as-is, flagged to the owner.
> - **The `<home-public-address>` rules (`53`/`61208`/`9876`, labelled `# Avery STL summer`) are for the REMOTE SITE, not home.** The IP currently matches home only because the owner is physically at the house (Softserve egress = `<home-public-address>`, ipify 2026-09-13); the setup returns to the remote site soon. Per the owner the remote site needs only **normal internet access (DNS via `:53`)**, not the full scale — so glances (`:61208`) was correctly dropped for it; the `:53` DNS allow stays.

The droplets operate exclusively on DNS (port **53**) via the Cloud Firewall. Ports **80** and **443** remain closed, with port **443** explicitly permitted only for operational access.
- SSH access is restricted to keys only.
- LE operates using **DNS-01**; opening port 80 is unnecessary as a result.

## Request Domain Configuration and Access Rules

### Pi-hole Admin Interface
Pi-hole administrative interface is restricted to Tailscale-only access. Ports `80` and `443` are disabled for the admin panel; the interface serves on ports `8080` (HTTP) and `8443` (HTTPS).
- **URL:** `http://<droplet-tailscale-address>:8080/admin`
- DNS service (`:53`) and Avery services remain unaffected by this port change.

### Caddy Reverse Proxy
Caddy v2.11.4 is installed natively via `apt` on the droplet, managed by systemd. It listens on ports `80` and `443`.
- **Global Config:** Email set to `<email>` in `/etc/caddy/Caddyfile`.
- **Site Block:**
  ```text
  request.example.org {
    reverse_proxy <sundae-address>:5055
  }
  ```
  This configuration proxies traffic to Seerr over Tailscale.

### TLS and Certificate Management
Caddy uses automatic TLS-ALPN-01 challenges for certificate issuance and renewal. DNS-01 challenges and custom Caddy builds with DO-DNS modules are not used, as ports `80` and `443` are publicly accessible.

### Firewall Rules (ufw)
- Ports `80/tcp` and `443/tcp` are opened publicly for IPv4 and IPv6.
- Port `53` remains IP-locked.
- SSH access is restricted to Tailscale-only connections.

### DNS Records
- `request.example.org` resolves via A record to `<droplet-address>`.
- `welcome.example.org` is live (friend onboarding tutorial), and **`portfolio.hawktopus.org` is live since 2026-09-14** (public engineering portfolio — A record → `<droplet-address>`, id 1832184755, served by the droplet's Caddy front door; see Hawktopus-Portfolio-Site). This supersedes the earlier note that `welcome`/`join` were unconfigured pending Phase 3.
