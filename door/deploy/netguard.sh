#!/bin/sh
# Owns Uhura's network namespace. New outbound connections to private ranges are refused; replies to
# inbound requests still flow. Rules are applied before the sleep, so a restart of this container
# always reapplies them, and if any rule fails the container exits instead of running unguarded.
set -e
iptables -F OUTPUT
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
# Uhura's one route into the lab: her inner half in Kirk's stack, which needs its own secret.
if [ -n "$LIAISON_HOST" ]; then
  iptables -A OUTPUT -d "$LIAISON_HOST" -p tcp --dport 8479 -j ACCEPT
fi
for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16 127.0.0.0/8 224.0.0.0/4 240.0.0.0/4; do
  iptables -A OUTPUT -d "$net" -j REJECT
done
iptables -A OUTPUT -j ACCEPT
if ip6tables -L OUTPUT >/dev/null 2>&1; then
  ip6tables -F OUTPUT
  ip6tables -A OUTPUT -o lo -j ACCEPT
  ip6tables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  ip6tables -A OUTPUT -d fc00::/7 -j REJECT
  ip6tables -A OUTPUT -d fe80::/10 -j REJECT
fi
echo "netguard: rules applied"
exec sleep infinity
