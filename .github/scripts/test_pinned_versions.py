import json
import tempfile
import unittest
from pathlib import Path

import pinned_versions as pv

SHA = "a" * 40


class NpmTest(unittest.TestCase):
    def test_exact_and_local_specs_pass(self) -> None:
        for spec in ["1.2.3", "1.2.3-beta.1", "workspace:*", "workspace:1.0.0", "npm:foo@2.0.0",
                     "file:../x", "link:../x", f"github:o/r#{SHA}"]:
            self.assertTrue(pv.npm_pinned(spec), spec)

    def test_ranges_fail(self) -> None:
        for spec in ["^1.2.3", "~1.2.3", "*", ">=1.0.0", "x", "1.x", "latest", "workspace:^",
                     "npm:foo@^2.0.0", "github:o/r#main"]:
            self.assertFalse(pv.npm_pinned(spec), spec)


class CargoTest(unittest.TestCase):
    def test_exact_and_local_specs_pass(self) -> None:
        for spec in ["=1.2.3", {"version": "=1.2.3", "features": ["x"]}, {"workspace": True},
                     {"path": "../x"}, {"path": "../x", "version": "=0.1.0"}, {"git": "u", "rev": SHA}]:
            self.assertTrue(pv.cargo_pinned(spec), spec)

    def test_ranges_fail(self) -> None:
        for spec in ["1.2", "1.2.3", "^1", "~1.2", "*", ">=1", {"version": "1"},
                     {"path": "../x", "version": "0.1"}, {"git": "u"}, {"git": "u", "branch": "main"}]:
            self.assertFalse(pv.cargo_pinned(spec), spec)


class PythonTest(unittest.TestCase):
    def test_exact_specs_pass(self) -> None:
        for req in ["pytest==8.0.0", "uvicorn[standard]==0.30.1", "a==1.0 ; python_version < '3.11'",
                    f"pkg @ git+https://x/y.git@{SHA}"]:
            self.assertTrue(pv.python_pinned(req), req)

    def test_ranges_fail(self) -> None:
        for req in ["pytest>=8.0.0", "a~=1.0", "a", "a<2", "a==1.0,<2", "pkg @ git+https://x/y.git@main"]:
            self.assertFalse(pv.python_pinned(req), req)


class PnpmLockTest(unittest.TestCase):
    def test_reads_inline_and_block_resolutions(self) -> None:
        text = (
            "lockfileVersion: '9.0'\n\npackages:\n\n"
            "  a@1.0.0:\n    resolution: {integrity: sha512-x}\n\n"
            "  b@1.0.0:\n    resolution:\n      integrity: sha512-y\n\n"
            "  '@c/d@1.0.0':\n    resolution: {}\n\n"
            "snapshots:\n\n  a@1.0.0: {}\n"
        )
        got = dict(pv.pnpm_resolutions(text))
        self.assertEqual(got, {"a@1.0.0": "{integrity: sha512-x}", "b@1.0.0": " integrity: sha512-y", "@c/d@1.0.0": "{}"})
        self.assertEqual(list(pv.check_pnpm_lock(_write(text, "pnpm-lock.yaml"), "l")), ["l  @c/d@1.0.0: missing integrity hash"])


def _write(text: str, name: str) -> Path:
    path = Path(tempfile.mkdtemp()) / name
    path.write_text(text)
    return path


class RepoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def put(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_reports_each_ecosystem_and_skips_ignored_dirs(self) -> None:
        self.put("package.json", json.dumps({"dependencies": {"a": "^1.0.0"}, "peerDependencies": {"p": "^1"}}))
        self.put("node_modules/x/package.json", json.dumps({"dependencies": {"a": "^1.0.0"}}))
        self.put("sub/package.json", json.dumps({"dependencies": {"a": "^1.0.0"}}))
        self.put(".gitmodules", '[submodule "sub"]\n\tpath = sub\n')
        self.put("crate/Cargo.toml", '[dependencies]\nserde = "1"\n[target.\'cfg(unix)\'.dependencies]\nlibc = "=0.2.1"\n'
                 '[workspace.dependencies]\ntokio = { version = "1.0" }\n')
        self.put("py/pyproject.toml", '[project]\nname="x"\ndependencies=["a>=1"]\n[project.optional-dependencies]\n'
                 'e=["b==1"]\n[dependency-groups]\ndev=["c", {include-group = "e"}]\n[build-system]\nrequires=["hatchling"]\n')
        self.put("py/requirements-dev.txt", "# c\n-r base.txt\nd==1.0  # ok\ne>=2\n")
        count, errors = pv.check(self.root, [])
        self.assertEqual(count, 4)
        self.assertEqual(sorted(errors), sorted([
            'package.json  dependencies > "a": "^1.0.0" is not an exact version',
            'crate/Cargo.toml  [dependencies] serde: "1" is not an exact "=x.y.z" version',
            'crate/Cargo.toml  [workspace.dependencies] tokio: {"version": "1.0"} is not an exact "=x.y.z" version',
            'py/pyproject.toml  [project.dependencies] "a>=1" is not pinned with ==',
            'py/pyproject.toml  [dependency-groups.dev] "c" is not pinned with ==',
            'py/requirements-dev.txt:4  "e>=2" is not pinned with ==',
        ]))

    def test_exclude_and_parse_errors(self) -> None:
        self.put("vendor/package.json", json.dumps({"dependencies": {"a": "^1"}}))
        self.put("bad/Cargo.toml", "[dependencies\n")
        _, errors = pv.check(self.root, ["vendor"])
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("bad/Cargo.toml: cannot parse"))

    def test_npm_lock_integrity(self) -> None:
        self.put("package-lock.json", json.dumps({"lockfileVersion": 3, "packages": {
            "": {}, "node_modules/a": {"resolved": "https://registry.npmjs.org/a.tgz"},
            "node_modules/b": {"resolved": "https://registry.npmjs.org/b.tgz", "integrity": "sha512-x"},
            "node_modules/c": {"link": True}}}))
        _, errors = pv.check(self.root, [])
        self.assertEqual(errors, ["package-lock.json  node_modules/a: missing integrity hash"])

    def test_main_exit_codes(self) -> None:
        self.put("package.json", json.dumps({"dependencies": {"a": "1.0.0"}}))
        self.assertEqual(pv.main(["--root", str(self.root)]), 0)
        self.put("package.json", json.dumps({"dependencies": {"a": "latest"}}))
        self.assertEqual(pv.main(["--root", str(self.root)]), 1)


if __name__ == "__main__":
    unittest.main()
