# Hawktopus Blog Site — blog.hawktopus.org (public write-ups: how to deploy, update or roll back a post)

## Serving

- **DNS:** DigitalOcean A record `blog` → `<droplet-address>` (the DO droplet), ttl 300, **record id 1837379429**, created
  2026-11-04 via the DO API (token in `Reference\Credentials\network-credentials.md` § DigitalOcean). It overrides the
  `*.example.org` wildcard, which points home.
- **Caddy** on the droplet (`ssh do-pihole`), block in `/etc/caddy/Caddyfile`, an exact copy of the portfolio's:
  ```
  blog.hawktopus.org {
  	root * /var/www/blog
  	try_files {path} {path}.html
  	file_server
  }
  ```
  `try_files` is what makes `/home-ai` serve `home-ai.html`. Backup taken before the edit:
  `/etc/caddy/Caddyfile.bak-blog-20261104T140051Z`. Cert: Let's Encrypt via Caddy's default ACME (verified 2026-11-04,
  issuer YE1, valid to 2027-02-02). HTTP/3 works because the droplet's ufw already allows 443/udp (portfolio doc).
- **Doc root:** `/var/www/blog` (`caddy:caddy`): `index.html` (post list) + `home-ai.html`.

## Rollback

`ssh do-pihole "cp /etc/caddy/Caddyfile.bak-blog-20261104T140051Z /etc/caddy/Caddyfile && caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy"`
removes the site (other blocks unchanged since the backup only if nothing else edited the Caddyfile after it — check
with `diff` first). DNS can stay (it will serve nothing) or be deleted by record id 1837379429.

