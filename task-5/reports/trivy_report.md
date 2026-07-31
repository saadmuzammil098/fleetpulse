# Task 5 — Trivy Scan Report

Scanned with `aquasec/trivy:latest` (run via Docker, no local install needed)
against `fleetpulse-api:latest`, `--severity HIGH,CRITICAL`. Full raw table
output: [`trivy_scan_output.txt`](./trivy_scan_output.txt).

## Before hardening

23 findings, all in base-Debian OS packages (none in the Python
dependency tree): 4 **CRITICAL** + 7 HIGH in `perl-base` alone (Perl
regex/Archive-Tar/Storable CVEs), the rest spread across `login`,
`util-linux`, `ncurses-*`.

## What was fixed

`perl-base`, `login`, `util-linux`, `ncurses-bin` are not used by a
headless Python API process at runtime — they're base-Debian defaults for
interactive/admin use this container never does. Purging them
(`apt-get purge -y --allow-remove-essential ...` in the runtime stage,
before switching to the non-root user) requires the `--allow-remove-essential`
flag since apt/dpkg mark these "essential" and refuse plain `--auto-remove`
silently rather than erroring loudly — a real footgun: an earlier attempt
using `; rm -rf ...` instead of `&& rm -rf ...` let apt's silent failure
slide through and *look* like it worked (build succeeded, image unchanged).
Chaining with `&&` and confirming the actual package removal in the build
log is what caught it.

**Result: 23 → 12 findings. All 4 CRITICAL eliminated.**

## What's left, and why it's not further reduced

The remaining 12 are all HIGH, all in `libtinfo6`, `libacl1`, `libblkid1`,
`libmount1`, `libsmartcols1`, `libuuid1`, `liblastlog2-2`, `ncurses-base`,
`bsdutils`, `mount`, `gzip`. These are **hard runtime dependencies of
`bash` and `coreutils`** — confirmed by actually attempting to purge them
too:

```
bash : PreDepends: libtinfo6 (>= 6) but it is not going to be installed
coreutils : PreDepends: libacl1 (>= 2.2.23) but it is not going to be installed
```

Removing them breaks the base image outright (no working shell, no
`cp`/`mkdir`/etc. for the multi-stage COPY steps and HEALTHCHECK's Python
invocation to rely on indirectly). None have a fixed version available
from Debian yet as of this scan (`Fixed Version` column empty in the raw
Trivy table) — there is no available patch to apply even if we wanted one
instead of removing the package. This is the practical floor for a
`python:3.12-slim` (Debian) base without moving to a fundamentally
different base image (e.g. distroless or Alpine/musl), which was judged
out of scope here given scikit-learn/pandas/numpy/scipy's wheel
availability and build-toolchain risk on musl — a real tradeoff, not an
oversight, and worth a full day of its own rather than a Day-5 side quest.

**Risk acceptance:** all 12 remaining findings are in OS-level libraries
with no listening network service, invoked only indirectly (by `bash`,
`coreutils`, `apt` during build) and never by attacker-reachable
request-handling code paths in this image — the container runs as a
non-root user, exposes only the FastAPI app on 8811, and never executes
gzip/mount/login functionality as part of request handling. Reassess when
Debian ships fixed versions.
