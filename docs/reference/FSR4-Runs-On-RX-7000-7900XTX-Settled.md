# FSR 4 runs on the RX 7900 XTX (RX 7000 / RDNA 3) — settled, official since FSR SDK 2.4; "RX 9000 only" pages are outdated

**Settled fact. Do not re-research it, and do not report "FSR 4 needs an RX 9000" as a finding.** the owner, 2026-11-01:
*"it's been established for nearly a year it feels like that we can unofficially make fsr 4 work on my 7900xtx, and
officially it came out earlier this year. You, myself, and chat gpt have all flagged that multiple times and it keeps
being brought up."*

- **Official:** AMD FSR SDK 2.4.0 ships FSR Upscaling 4.1.2 (ML), dated 2026-10-28. Its manual page
  (gpuopen.com/manuals/fsr_sdk/techniques/super-resolution-ml/) says FSR 4 *"requires an AMD Radeon RX 9000, RX 7000,
  RX 6000 Series discrete GPU"* and lists the **RX 7900 XTX on the Full INT8 model at 4K**. RX 9000 cards use FP8,
  RX 7000/6000 use INT8.
- **Unofficial, before that:** the community ran FSR 4 on RDNA 3 for about a year (the owner's lived history).
- **What IS RX 9000-only:** FSR Frame Generation 4.0.2 and Driver-Assisted Frame Pacing. The 7900 XTX gets Frame
  Generation 3.1.7 (Swapchain). Pass 1's audit: [[Jade-Empire-39-Research-Pass-1-2026-10-29]].
- **Why it keeps coming back:** AMD's older FSR 4 launch pages still say RX 9000 only, and web research keeps finding
  them first. HAI's #39 night run (2026-11-01) repeated it from such a page. When a source says otherwise, check its
  date against the SDK 2.4.0 manual above. The manual wins.

Lab hardware this covers: Mothership's RX 7900 XTX (the #39 Jade Empire FSR 4 target, [[39-CHARTER]]).
