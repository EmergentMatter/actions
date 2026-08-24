# Security Policy

## Scope

This policy covers software authored by EmergentMatter: the
`emergent-matter-*` packages, `EmergentMatter/actions`, and the
SDM toolchain.

This organization also hosts forks of third-party open-source
projects — for example `optax`, `diffrax`, `equinox`, `jax-fem`
and `numpyro`. These are frozen snapshots kept for
reproducibility. EmergentMatter does not maintain them. Please
report issues and vulnerabilities in those projects to their
upstream maintainers.

## Reporting a vulnerability

**Preferred:** report using GitHub's private vulnerability reporting on
the affected repository -- go to that repository, open
**Security → Report a vulnerability**, and submit the report there.

**Fallback:** if the affected repository doesn't offer private
vulnerability reporting, report it at
[EmergentMatter/actions' security advisories](https://github.com/EmergentMatter/actions/security/advisories/new)
instead, and name the affected package or repository in the report.

**Do not open a public issue for a security report**, on either route.
Private reporting keeps the details out of view until a fix is
available.

We aim to acknowledge within five business days. Tarek El Afifi is the
responder of record.

### What to include

- The affected package and version
- Steps to reproduce
- The impact -- what an attacker can do with it

## Supported versions

These packages are pre-1.0. Only the latest release of each package is
supported; there are no backported fixes to older versions.
