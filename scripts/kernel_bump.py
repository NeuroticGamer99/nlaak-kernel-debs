#!/usr/bin/env python3
"""Stage a pinned-kernel release, or report the newest kernel backports offers.

This file lives in the homelab repository (ansible/roles/base_linux/files/
kernel-debs-repo/scripts/) and is copied, with the rest of that folder, to the
public repository that holds the releases. It uses the standard library plus
gpgv.

  kernel_bump.py stage <version> <snapshot> <outdir> [--keyring FILE ...]
      <version>   the package version, e.g. 7.1.13-1~bpo13+1
      <snapshot>  "live" to stage a kernel the live backports index still
                  offers whole (no dependence on snapshot.debian.org), "now" for
                  the snapshot at this moment, or a snapshot.debian.org
                  timestamp at which that kernel's packages were all in the
                  trixie-backports index (for a kernel that has been replaced)
      Downloads every package that exists only at that version and writes the
      files for the GitHub release, plus <outdir>/<tag>.defaults.yml (the
      variables for base_linux) and <outdir>/tag.txt. The tag is "kernel-" and
      the whole Debian version, so a new revision of a kernel never collides.

  kernel_bump.py latest [--keyring FILE ...]
      Reports the kernel version the live backports index offers, whether its
      packages are all present, and the release tag it would use.

Trust chain: the Release file is verified against Debian's archive keyring
with gpgv, the Packages index is checked against the hash that Release lists,
and every package is checked against the hash that Packages lists. Anything
that fails stops the run before a file is written.
"""
import argparse
import datetime
import hashlib
import lzma
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

SNAPSHOT = "https://snapshot.debian.org/archive/debian/{ts}"
LIVE = "https://deb.debian.org/debian"
SUITE = "/dists/trixie-backports"
PACKAGES = "main/binary-amd64/Packages.xz"
KEYRINGS = [
    "/usr/share/keyrings/debian-archive-keyring.gpg",
    "/usr/share/keyrings/debian-archive-keyring.pgp",
    "/usr/share/keyrings/debian-archive-removed-keys.gpg",
    "/usr/share/keyrings/debian-archive-removed-keys.pgp",
]


def fetch(url):
    with urllib.request.urlopen(url, timeout=120) as resp:
        return resp.read()


def verify_release(inrelease, keyrings):
    """Check a clearsigned InRelease with gpgv and return the signed text."""
    keyrings = [k for k in keyrings if Path(k).exists()]
    if not keyrings:
        sys.exit("no Debian archive keyring found; install debian-archive-keyring or pass --keyring")
    with tempfile.TemporaryDirectory() as tmp:
        signed, text = Path(tmp, "InRelease"), Path(tmp, "Release")
        signed.write_bytes(inrelease)
        cmd = ["gpgv", "--status-fd", "1", "--output", str(text)]
        for keyring in keyrings:
            cmd += ["--keyring", keyring]
        result = subprocess.run(cmd + [str(signed)], capture_output=True, text=True, check=False)
        if result.returncode != 0 or "[GNUPG:] VALIDSIG" not in result.stdout:
            sys.exit(f"InRelease signature check failed:\n{result.stdout}{result.stderr}")
        return text.read_text(encoding="utf-8")


def release_entry(release_text, name):
    """The (sha256, size) the signed Release file lists for a path."""
    in_section = False
    for line in release_text.splitlines():
        if line.startswith("SHA256:"):
            in_section = True
        elif in_section and not line.startswith(" "):
            in_section = False
        elif in_section:
            digest, size, path = line.split()
            if path == name:
                return digest, int(size)
    sys.exit(f"the signed Release file does not list {name}")


def load_index(base, keyrings):
    """Fetch and fully verify the backports Packages index under a base URL."""
    release = verify_release(fetch(base + SUITE + "/InRelease"), keyrings)
    digest, size = release_entry(release, PACKAGES)
    raw = fetch(f"{base}{SUITE}/{PACKAGES}")
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
        sys.exit("the Packages index does not match the hash in the signed Release file")
    return parse(lzma.decompress(raw).decode("utf-8"))


def parse(text):
    packages = {}
    for block in text.split("\n\n"):
        fields, key = {}, None
        for line in block.splitlines():
            if line[:1] in (" ", "\t"):
                fields[key] += "\n" + line
            elif ":" in line:
                key, _, value = line.partition(":")
                fields[key] = value.strip()
        if "Package" in fields:
            packages[fields["Package"]] = fields
    return packages


ROOTS = ("linux-image-amd64", "linux-headers-amd64")
DEPENDENCY = re.compile(r"\s*([a-z0-9+.\-]+)\s*(?:\(\s*(<<|<=|=|>=|>>)\s*([^)\s]+)\s*\))?")


def dependency_groups(field):
    """A Depends field as a list of alternatives: [[(name, operator, version)]]."""
    groups = []
    for part in field.split(","):
        alternatives = [m.groups() for m in map(DEPENDENCY.match, part.split("|")) if m]
        if alternatives:
            groups.append(alternatives)
    return groups


def closure(packages, version):
    """The packages that make up this kernel version, and what is missing.

    Starts from the two meta-packages, which must both be in the index at this
    exact version. After that every dependency is resolved against its own
    constraint, not against the root's version: a kernel package may pin a
    binary rebuild (7.1.13-1~bpo13+1+b1) while the meta-packages do not.

    A group of alternatives made only of exact pins on linux-* packages
    ("a (= X) | b (= Y)") is part of the set and must be satisfied: one
    alternative that the index holds at its pinned version is enough, and the
    first such one is used. A group no alternative can satisfy is reported as
    missing, its names joined with " | ". Any other dependency (initramfs-tools
    and the like) comes from the main archive, and is added only if the index
    happens to hold it at the root version.
    """
    found, missing = {}, set()
    queue = []
    for root in ROOTS:
        pkg = packages.get(root)
        if pkg is None or pkg.get("Version") != version:
            missing.add(root)
        else:
            queue.append(root)
    while queue:
        name = queue.pop()
        if name in found:
            continue
        found[name] = packages[name]
        for alternatives in dependency_groups(found[name].get("Depends", "")):
            if all(op == "=" and dep.startswith("linux-") for dep, op, _ in alternatives):
                satisfied = [dep for dep, _, wanted in alternatives
                             if dep in packages and packages[dep].get("Version") == wanted]
                if satisfied:
                    queue.append(satisfied[0])
                else:
                    missing.add(" | ".join(dep for dep, _, _ in alternatives))
            else:
                queue.extend(dep for dep, _, _ in alternatives
                             if dep in packages and packages[dep].get("Version") == version)
    return found, missing


def download(url, dest, sha256):
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=600) as resp, open(dest, "wb") as out:
        while chunk := resp.read(1 << 20):
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest() != sha256:
        dest.unlink()
        sys.exit(f"sha256 mismatch for {dest.name}: index says {sha256}, got {digest.hexdigest()}")


def tag_for(version):
    """The whole Debian version, so a new revision of a kernel gets its own tag."""
    return "kernel-" + re.sub(r"[^A-Za-z0-9.]+", "-", version).strip("-")


def stage(args):
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # "live" stages from the live mirror's verified index, for a kernel that is
    # still current; it does not depend on snapshot.debian.org being reachable.
    # "now" and a timestamp go through the snapshot service.
    live = args.snapshot == "live"
    timestamp = now if args.snapshot in ("live", "now") else args.snapshot
    base = LIVE if live else SNAPSHOT.format(ts=timestamp)
    source = "the live index" if live else f"snapshot {timestamp}"

    packages = load_index(base, args.keyring)
    found, missing = closure(packages, args.version)
    if missing:
        sys.exit(f"{args.version} is incomplete in {source}; the index lacks: {', '.join(sorted(missing))}")

    tag = tag_for(args.version)
    out = Path(args.outdir)
    release = out / tag
    release.mkdir(parents=True, exist_ok=True)
    sums, yaml = [], []
    for name in sorted(found):
        pkg = found[name]
        # GitHub rewrites '~' and '+' in asset names, so the release uses '-'.
        asset = pkg["Filename"].rsplit("/", 1)[1].replace("~", "-").replace("+", "-")
        download(base + "/" + pkg["Filename"], release / asset, pkg["SHA256"])
        sums.append(f"{pkg['SHA256']}  {asset}")
        yaml.append(f"  - asset: {asset}\n    sha256: {pkg['SHA256']}")
        print(f"ok  {asset}  {int(pkg['Size']) / 1048576:.1f} MB")
    (release / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    note = (
        "# Staged from the live index. Use the first snapshot that lists the whole\n"
        "# set; until the snapshot service has one, installs use the release.\n"
        if live
        else ""
    )
    defaults = (
        f'base_linux_kernel_version: "{args.version}"\n'
        + note
        + f'base_linux_kernel_snapshot_timestamp: "{timestamp}"\n'
        f"base_linux_kernel_fallback_tag: {tag}\n"
        "base_linux_kernel_fallback_files:\n" + "\n".join(yaml) + "\n"
    )
    (out / (tag + ".defaults.yml")).write_text(defaults)
    (out / "tag.txt").write_text(tag + "\n")
    print(f"\n{len(found)} packages staged in {release}\nvariables in {out / (tag + '.defaults.yml')}")


def latest(args):
    packages = load_index(LIVE, args.keyring)
    version = packages["linux-image-amd64"]["Version"]
    found, missing = closure(packages, version)
    print(f"version={version}")
    print(f"tag={tag_for(version)}")
    print(f"complete={'false' if missing else 'true'}")
    if missing:
        print(f"missing={','.join(sorted(missing))}")
    print(f"packages={len(found)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("stage")
    one.add_argument("version")
    one.add_argument("snapshot")
    one.add_argument("outdir")
    two = sub.add_parser("latest")
    for p in (one, two):
        p.add_argument("--keyring", action="append", default=None)
    args = parser.parse_args()
    if args.keyring is None:
        args.keyring = KEYRINGS
    {"stage": stage, "latest": latest}[args.command](args)


if __name__ == "__main__":
    main()
