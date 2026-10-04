"""Tests for the parts of kernel_bump.py that decide what a release contains.

Run from the repository root:  python3 -m unittest discover -s tests -v
"""
import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "kernel_bump.py"
spec = importlib.util.spec_from_file_location("kernel_bump", SCRIPT)
kb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kb)

V = "7.1.13-1~bpo13+1"
ABI = "7.1.13+deb13-amd64"


def package(name, version=V, depends=""):
    return {"Package": name, "Version": version, "Depends": depends, "SHA256": "0" * 64,
            "Filename": f"pool/main/l/linux/{name}_{version}_amd64.deb", "Size": "1"}


def complete_index():
    """A kernel as the archive publishes it: two metas, an image, and its parts."""
    pkgs = [
        package("linux-image-amd64", depends=f"linux-image-{ABI} (= {V})"),
        package("linux-headers-amd64", depends=f"linux-headers-{ABI} (= {V})"),
        package(f"linux-image-{ABI}",
                depends=f"linux-base-{ABI} (= {V}), linux-binary-{ABI} (= {V}), "
                        f"linux-modules-{ABI} (= {V}), initramfs-tools (>= 0.140) | linux-initramfs-tool"),
        package(f"linux-headers-{ABI}",
                depends=f"linux-headers-7.1.13+deb13-common (= {V}), linux-kbuild-7.1.13+deb13 (= {V}), "
                        f"linux-image-{ABI} (= {V})"),
        package(f"linux-base-{ABI}"),
        package(f"linux-binary-{ABI}"),
        package(f"linux-modules-{ABI}"),
        package("linux-headers-7.1.13+deb13-common"),
        package("linux-kbuild-7.1.13+deb13"),
    ]
    return {p["Package"]: p for p in pkgs}


class Closure(unittest.TestCase):
    def test_a_complete_set_has_nothing_missing(self):
        found, missing = kb.closure(complete_index(), V)
        self.assertEqual(missing, set())
        self.assertEqual(len(found), 9)
        self.assertNotIn("initramfs-tools", found)

    def test_a_missing_headers_meta_package_is_incomplete(self):
        index = complete_index()
        del index["linux-headers-amd64"]
        _, missing = kb.closure(index, V)
        self.assertEqual(missing, {"linux-headers-amd64"})

    def test_a_missing_image_meta_package_is_incomplete(self):
        index = complete_index()
        del index["linux-image-amd64"]
        _, missing = kb.closure(index, V)
        self.assertEqual(missing, {"linux-image-amd64"})

    def test_a_dependency_missing_from_the_index_is_reported(self):
        index = complete_index()
        del index[f"linux-modules-{ABI}"]
        _, missing = kb.closure(index, V)
        self.assertEqual(missing, {f"linux-modules-{ABI}"})

    def test_a_dependency_at_another_revision_is_reported_not_skipped(self):
        index = complete_index()
        index[f"linux-modules-{ABI}"]["Version"] = "7.1.13-2~bpo13+1"
        found, missing = kb.closure(index, V)
        self.assertEqual(missing, {f"linux-modules-{ABI}"})
        self.assertNotIn(f"linux-modules-{ABI}", found)

    def test_metas_at_another_revision_are_not_this_kernel(self):
        index = complete_index()
        for name in ("linux-image-amd64", "linux-headers-amd64"):
            index[name]["Version"] = "7.1.13-2~bpo13+1"
        found, missing = kb.closure(index, V)
        self.assertEqual(found, {})
        self.assertEqual(missing, {"linux-image-amd64", "linux-headers-amd64"})


class Tags(unittest.TestCase):
    def test_revisions_of_one_upstream_kernel_get_different_tags(self):
        self.assertNotEqual(kb.tag_for("7.1.13-1~bpo13+1"), kb.tag_for("7.1.13-2~bpo13+1"))

    def test_a_tag_uses_only_characters_git_and_github_accept(self):
        self.assertEqual(kb.tag_for("7.1.13-1~bpo13+1"), "kernel-7.1.13-1-bpo13-1")
        self.assertEqual(kb.tag_for("7.0.12-2~bpo13+1"), "kernel-7.0.12-2-bpo13-1")


class DependencyParsing(unittest.TestCase):
    def test_alternatives_and_version_constraints(self):
        groups = kb.dependency_groups("a (= 1~x+2), b (>= 3) | c, d")
        self.assertEqual(groups, [[("a", "=", "1~x+2")], [("b", ">=", "3"), ("c", None, None)], [("d", None, None)]])


if __name__ == "__main__":
    unittest.main()
