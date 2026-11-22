# Fellowship Cluster - Build Configuration and Status



## Related Documentation
- Fleet roster and current status: `see [[Tablet-Cluster-INDEX]]`
- Scheduler implementation: `see orchestrator-build\scheduler.py`

## 6. Building Binaries for Fellowship Tablets

### Design Rule: Host Builds Only
Binary compilation for Fellowship tablets occurs exclusively within the Softserve VM environment (Sundae). Native builds on tablet hardware are prohibited due to resource constraints and reliability failures.

### ABI Compatibility Requirements
Tablets run Termux, which provides a `bionic` libc environment. The Softserve build host uses an arm64 Debian container via QEMU binfmt (`docker run --platform linux/arm64`). This container provides a `glibc` environment.

- **Glibc binaries** produced by the Softserve VM do not execute natively on Termux `bionic`. Execution fails with: `Could not find a PHDR: broken executable?`.
- **Valid targets:**
  1. Build glibc-compatible binaries for execution within the tablets' `proot Debian` environment.
  2. Build bionic-compatible binaries natively within Termux (reserved for small workloads, e.g., HPL).

### Conformance
See PROJECT-BACKLOG §aarch64 build factory for the automated build infrastructure design.

## Fellowship Crunch Integration Status

Full HPL (Top500 benchmark) builds and runs on the fleet in single-node and distributed configurations with residuals PASSED. Open MPI 4.1.5 launches ranks across tablets over WiFi.

Benchmark results for STREAM, iperf3, and per-node HPL are recorded in `Lab\fellowship\cluster\RESULTS-*.md`.
