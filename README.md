# nlaak-kernel-debs

Copies of the Debian `trixie-backports` kernel packages that the Nlaak homelab
pins, kept as releases so a rebuild never depends on Debian's backports index
still listing them.

Each release holds the `.deb` files for one kernel and a `SHA256SUMS`. It is
tagged `kernel-` and the whole Debian version with `-` for `~` and `+`, so
`7.1.13-2~bpo13+1` is `kernel-7.1.13-2-bpo13-1` and a later revision of a kernel
never collides with an earlier one. (The first release, `kernel-7.1.13`, was
published by hand before this naming and keeps its tag.) File names use the same
substitution, because GitHub rewrites `~` and `+`. The packages are unmodified
Debian builds.

## Trust

Nothing here is trusted on its own. The homelab repository records a sha256 for
every file and refuses any file that differs, so replacing a release cannot get
a different package installed. Those sha256 values are taken from Debian's
Packages index, which `scripts/kernel_bump.py` checks against the signed `InRelease`
with Debian's archive keyring before it downloads anything. Debian signs
`InRelease` with several archive keys so older keyrings keep working; like apt,
the script needs at least one good signature from a key it trusts, and refuses
a release with any bad signature. Releases are immutable.

## Workflows

- **Publish a kernel release** (manual): takes a package version and a source,
  `live` for a kernel the live backports index still offers whole (this does
  not depend on snapshot.debian.org) or a snapshot.debian.org timestamp for one
  that has been replaced. It verifies the signature chain, requires both
  meta-packages and, for every exact version pin among the kernel's
  dependencies, a package at the pinned version, and publishes the
  release only once all of its files are attached. A draft left by an
  interrupted run is deleted and the upload restarted; a published release is
  never touched. It prints the variables to paste into the homelab repository.
- **Watch for a new backports kernel** (weekly): opens an issue when backports
  offers a complete kernel with no published release here. It publishes nothing
  and changes nothing in the homelab repository. It deliberately does not
  archive every kernel on its own: releases are immutable and permanent, and
  only a kernel the lab has chosen to pin needs a copy.

## Layout

```
.github/workflows/release.yml   publish a release (manual)
.github/workflows/watch.yml     weekly check for a new kernel
scripts/kernel_bump.py          the only code: stage a release, or report the latest kernel
tests/test_kernel_bump.py       what counts as a complete kernel, and the tag names
README.md
```

Run the tests with `python3 -m unittest discover -s tests -v` from the repository
root.

The canonical copy of everything above is in the homelab repository, under
`ansible/roles/base_linux/files/kernel-debs-repo/`, with the same layout. Copy
the whole folder here when anything changes.

## Keep it safe

Two-factor authentication on the owning account, write access for the two
maintainers only, release immutability on, and Actions allowed to run only
workflows from this repository.
