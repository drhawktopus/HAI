# Network Infrastructure

This domain documents the physical and logical network topology — machine inventory, connectivity
methods, and local service architectures. It is the primary reference for how hosts are identified,
connected, and accessed within the homelab.

## Common questions

- Which machines operate on the tailnet and what are their roles? → see [[Network-Homelab-Machine-Inventory-Virtualization]]
- How are DHCP and DNS services configured locally? → see [[Network-Local-Network-Services-DHCP-DNS-Architecture]]
- Is remote traffic encrypted via VPN, and how is tunneling applied? → see [[Network-External-Connectivity-Remote-Access]]
- Where are SMB shares and drive mappings defined? → see [[Network-Storage-Infrastructure-Drives-SMB-Shares]]
- What are the prerequisites for physical access to basement desktops? → see [[HOST-Basement-Desktops-Physical-Access-Linux-Track-Proced]]
- How should remote SSH operations be executed for basement hosts? → see [[HOST-Basement-Desktops-Remote-SSH-Operations-B]]
- What dictates hostname stability and Wake-on-LAN migration? → see [[HOST-Basement-Desktops-Hostname-Migration-Wake-on-LAN-Doc]]

## Pages in this domain

- **[[Network-Homelab-Machine-Inventory-Virtualization]]** — Each machine's role, network identity, OS, and Tailscale presence.
- **[[Network-Local-Network-Services-DHCP-DNS-Architecture]]** — Local DHCP and DNS configuration on the RS300 router.
- **[[Network-External-Connectivity-Remote-Access]]** — ExpressVPN containerization and traffic-routing rules, including qBittorrent tunneling.
- **[[Network-Storage-Infrastructure-Drives-SMB-Shares]]** — Drive mappings and SMB share configuration.
- **[[HOST-Basement-Desktops-Host-Inventory-Execution-Status]]** — Attribute tables for basement hosts (`rigby`, `mordecai`).
- **[[HOST-Basement-Desktops-Physical-Access-Linux-Track-Proced]]** — Physical-presence requirements for initial onboarding.
- **[[HOST-Basement-Desktops-Remote-SSH-Operations-B]]** — Remote-access tooling requirements (Bash over PowerShell).
- **[[HOST-Basement-Desktops-Capacity-Determination-Pending-Dec]]** — How capacity limits are derived from system probes.
- **[[HOST-Basement-Desktops-Hostname-Migration-Wake-on-LAN-Doc]]** — Why hostnames matter for DNS, SSH, and task scheduling; WoL doctrine.
- **[[HOST-Basement-Desktops-Procedure-Order-Provenance-Notes]]** — Onboarding order and hostname-assignment gates.
