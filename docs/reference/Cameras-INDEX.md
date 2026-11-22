# Cameras

The camera domain covers the Lorex N844A8 NVR and its two E841CA cameras. There is no central
Frigate instance — Frigate was removed 2026-08-12. Stream decoding and object detection are
distributed to the Fellowship tablets under the Tolkien head, which subscribes to the NVR's
hardware motion events on its private PoE subnet.

## Common questions

- How does the current detection pipeline work (NVR motion → tablet hardware-decode → ncnn / Tolkien)? → see [[Lorex-Camera-Architecture-Fellowship-Deployment]]
- What do the cameras run, and what can they run (resolution, codec, bitrate)? → see [[Lorex-Hardware-Network-Stream-Specs]] and [[RUNBOOK-Lorex-NVR-Operation]]
- Which room is each channel, and which subjects matter? → see [[Lorex-Detection-Strategy-Subject-Definitions]]
- Where do motion events go after the NVR triggers them? → see [[Lorex-Event-Routing-Motion-Payload-Logic]]
- How long is footage retained, and what tools manage the NVR? → see [[Lorex-Data-Retention-Storage-Tooling-Inventory]]
- How do I change or verify an encoder setting safely (and after a camera move)? → see [[Lorex-NVR-Configuration-Management-and-Bitrate-Tuning]]
- What is the integration scope and which APIs does the NVR expose? → see [[Lorex-NVR-Integration-Scope-and-API-Reference]]
- How do I operate the NVR day-to-day? → see [[RUNBOOK-Lorex-NVR-Operation]]

## Pages in this domain

- **[[Lorex-Hardware-Network-Stream-Specs]]** — N844A8 hardware, the private PoE subnet, stream geometry / codec / bitrate, and the on-camera detection capability.
- **[[Lorex-Detection-Strategy-Subject-Definitions]]** — detection priorities (feline activity), per-subject IR signatures, and channel coverage.
- **[[Lorex-Camera-Architecture-Fellowship-Deployment]]** — the current architecture: NVR motion → Fellowship hardware-decode → ncnn detection, with Tolkien as the head. Frigate is the retired predecessor.
- **[[Lorex-Event-Routing-Motion-Payload-Logic]]** — how motion events are routed through the classification paths.
- **[[Lorex-Data-Retention-Storage-Tooling-Inventory]]** — retention design and the NVR tooling inventory.
- **[[Lorex-NVR-Integration-Scope-and-API-Reference]]** — integration scope and the NVR API / endpoint reference.
- **[[Lorex-NVR-Configuration-Management-and-Bitrate-Tuning]]** — changing and verifying encoder settings, bitrate tuning, and how a camera move affects coordinates.

---

**Operator authority:** [[RUNBOOK-Lorex-NVR-Operation]] is the current operator manual for this
domain. **At a glance:** Frigate removed 2026-08-12 — the tablets decode and the Fellowship detects;
ch1 = Dining Room, ch2 = Basement; main stream 3072x1728 @30 HEVC on both channels.
