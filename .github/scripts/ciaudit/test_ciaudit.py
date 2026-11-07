"""Unit tests for the CI coverage audit. Run: python3 -m unittest (from this directory)."""

from __future__ import annotations

import io
import json
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path

import audit
import fetch
from cmds import Ctx, Hit, Interp, name_categories, segments, split_functions
from render import render
from units import Mounts, discover, in_workspace, load_jsonc
from verify import Check, Verifier, job_matches
from workflows import WILD, Evidence, StepRef, Walker, triggers_of


def write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text))


MONOREPO = {
    "package.json": json.dumps({"name": "root", "private": True, "scripts": {"lint": "biome check .", "test": "turbo run test"}}),
    "pnpm-workspace.yaml": "packages:\n  - packages/*\n",
    "turbo.json": '{ // jsonc\n "tasks": { "test": {"dependsOn": ["^build"]}, "build": {} } }',
    "packages/a/package.json": json.dumps(
        {"name": "a", "scripts": {"build": "tsc", "test": "vitest run"}, "dependencies": {"b": "workspace:*"}}
    ),
    "packages/a/tsconfig.json": "{}",
    "packages/a/src/x.ts": "export const x = 1\n",
    "packages/b/package.json": json.dumps({"name": "b", "scripts": {"build": "tsc"}}),
    "packages/b/tsconfig.json": "{}",
    "tools/check.sh": """\
        #!/bin/bash
        runIt() {
          cd "$parentPath"/../packages/b || {
            exit 1
          }
          npx vitest run
        }
        parentPath=$(cd "$(dirname "$0")" && pwd)
        runIt
        """,
    "svc/Dockerfile": "FROM scratch\n",
    "research/a.py": "print(1)\n",
    ".github/workflows/ci.yml": """\
        on: [pull_request]
        jobs:
          test:
            name: Test
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@v4
              - name: Unit
                run: pnpm turbo run test --filter a
              - name: Lint
                run: pnpm run lint
              - run: bash tools/check.sh
        """,
    ".github/workflows/release.yml": """\
        on: [workflow_dispatch]
        jobs:
          img:
            runs-on: ubuntu-latest
            steps:
              - name: Build image
                run: docker build -f svc/Dockerfile svc
        """,
}


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "stage-r"
        write(self.root, MONOREPO)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def walk(self) -> tuple[list, Walker, Ctx]:
        units = discover(self.root, "r")
        ctx = Ctx(self.root, units)
        w = Walker(ctx, "r", {"r": self.root})
        w.run_all(self.root / ".github" / "workflows")
        return units, w, ctx


class DiscoverTest(Fixture):
    def test_units(self) -> None:
        keys = {u.key for u in discover(self.root, "r")}
        for k in (
            "r:node:.",
            "r:node:packages/a",
            "r:node:packages/b",
            "r:docker:svc/Dockerfile",
            "r:python:research",
            "r:workflows:.github/workflows",
        ):
            self.assertIn(k, keys)

    def test_mounts_assign_owner(self) -> None:
        m = Mounts("outer", {"sub": "inner"})
        self.assertEqual(m.owner("sub/pkg"), ("inner", "pkg"))
        self.assertEqual(m.owner("sub"), ("inner", "."))
        self.assertEqual(m.owner("other"), ("outer", "other"))

    def test_jsonc_and_workspace(self) -> None:
        self.assertEqual(load_jsonc('{"a": "//x", /* c */ "b": [1,],}'), {"a": "//x", "b": [1]})
        self.assertTrue(in_workspace("packages/a", ["packages/*", "!packages/z"]))
        self.assertFalse(in_workspace("packages/z", ["packages/*", "!packages/z"]))
        self.assertFalse(in_workspace("captcha/integration/frameworks/vue/wrapper", ["captcha/integration/*"]))
        self.assertTrue(in_workspace("captcha/integration/frameworks/vue/wrapper", ["captcha/integration/**"]))
        self.assertTrue(in_workspace("captcha/integration/build-config", ["captcha/integration/*/"]))


class ShellTest(unittest.TestCase):
    def test_segments_lift_substitutions(self) -> None:
        segs = segments('X=$(echo hi) && foo "a;b" | bar # c')
        self.assertEqual(segs, ["echo hi", "X=SUBST", 'foo "a;b"', "bar"])

    def test_split_functions(self) -> None:
        main, bodies = split_functions("f() {\n  if x; then\n  {\n  }\n  fi\n  echo in\n}\nf\n")
        self.assertEqual(main.strip(), "f")
        self.assertIn("echo in", bodies["f"])

    def test_name_categories(self) -> None:
        self.assertEqual(name_categories("lint:fix"), set())
        self.assertIn("test", name_categories("test:unit"))
        self.assertIn("e2e", name_categories("test:e2e"))


class WalkTest(Fixture):
    def test_hits(self) -> None:
        _, w, _ = self.walk()
        got = {(e.hit.unit, e.hit.category, e.ref.workflow) for e in w.evidence}
        self.assertIn(("r:node:packages/a", "test", "ci.yml"), got)
        self.assertIn(("r:node:packages/b", "build", "ci.yml"), got, "turbo ^build reaches the dependency")
        self.assertIn(("r:node:packages/b", "test", "ci.yml"), got, "cd inside a shell function is followed")
        self.assertIn(("r:docker:svc/Dockerfile", "build", "release.yml"), got)
        self.assertNotIn(("r:node:packages/b", "test", "release.yml"), got)

    def test_triggers(self) -> None:
        self.assertEqual(triggers_of({True: ["push"]}), ("push",))

    def test_interp_php_vendor_bin(self) -> None:
        write(self.root, {"p/composer.json": "{}"})
        units = discover(self.root, "r")
        ctx = Ctx(self.root, units)
        Interp(ctx).run("php vendor/bin/pest", "p")
        self.assertIn(("r:php:p", "test"), {(h.unit, h.category) for h in ctx.hits})

    def test_npm_workspaces_runs_every_member(self) -> None:
        write(
            self.root,
            {
                "package.json": json.dumps({"name": "root", "workspaces": ["pkgs/*"], "scripts": {"typecheck": "tsc --noEmit"}}),
                "pkgs/x/package.json": json.dumps({"name": "x", "scripts": {"typecheck": "tsc --noEmit"}}),
                "pkgs/y/package.json": json.dumps({"name": "y", "scripts": {"build": "vite build"}}),
            },
        )
        (self.root / "pnpm-workspace.yaml").unlink()
        for cmd, want in (
            ("npm run typecheck --workspaces --if-present", {"r:node:pkgs/x"}),
            ("npm run typecheck -ws --include-workspace-root", {"r:node:pkgs/x", "r:node:."}),
        ):
            ctx = Ctx(self.root, discover(self.root, "r"))
            Interp(ctx).run(cmd, ".")
            self.assertEqual(want, {h.unit for h in ctx.hits if h.category == "typecheck"}, cmd)


class PythonTestRunnerTest(unittest.TestCase):
    def test_credits_dirs_with_tests_except_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(
                root,
                {
                    ".github/actions/a/a.py": "",
                    ".github/actions/a/test_a.py": "",
                    ".github/actions/b/b.py": "",
                    ".github/fixtures/f/test_f.py": "",
                    ".github/scripts/run_python_tests.py": "",
                },
            )
            ctx = Ctx(root, discover(root, "r"))
            Interp(ctx).run("python3 .github/scripts/run_python_tests.py --root .github", ".")
            tested = {h.unit for h in ctx.hits if h.category == "test"}
            self.assertIn("r:python:.github/actions/a", tested)
            self.assertNotIn("r:python:.github/actions/b", tested)
            self.assertFalse(any("fixtures" in t for t in tested))

    def test_uvx_pinned_tools_and_pytest_option_values(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root, {"a/a.py": "", "a/tests/test_a.py": "", "b/b.py": ""})
            ctx = Ctx(root, discover(root, "r"))
            Interp(ctx).run("uvx --python 3.12 ruff@0.17.2 check .", ".")
            Interp(ctx).run("uvx pytest@9.2.0 -q -p no:cacheprovider a/tests", ".")
            self.assertEqual({"r:python:a", "r:python:b"}, {h.unit for h in ctx.hits if h.category == "lint"})
            self.assertEqual({"r:python:a"}, {h.unit for h in ctx.hits if h.category == "test"})
            self.assertEqual([], ctx.unresolved)


class FakeApi:
    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, path: str) -> object:
        self.calls.append(path)
        for prefix, val in self.routes.items():
            if path.startswith(prefix):
                return val
        return None


def ref(step: str = "Unit", job: tuple[str, ...] = ("Test",), index: int = 1) -> StepRef:
    return StepRef("r", "ci.yml", ("pull_request",), job, step, index)


class VerifyTest(unittest.TestCase):
    RUN = {"workflow_runs": [{"id": 7, "html_url": "https://run/7"}]}

    def verifier(self, steps: list[dict], name: str = "Test") -> tuple[Verifier, FakeApi]:
        api = FakeApi(
            {
                "repos/o/r/actions/workflows/ci.yml/runs": self.RUN,
                "repos/o/r/actions/runs/7/jobs": {"jobs": [{"name": name, "html_url": "https://job", "steps": steps}]},
            }
        )
        return Verifier(api, "o", {"r": "main"}), api

    def test_success_by_name(self) -> None:
        v, api = self.verifier([{"name": "Unit", "number": 3, "conclusion": "success"}])
        c = v.check(ref())
        self.assertEqual((c.status, c.job_url, c.source), ("success", "https://job", "main"))
        self.assertIn("branch=main", api.calls[0])

    def test_skipped_by_index(self) -> None:
        v, _ = self.verifier([{"name": "Run pnpm", "number": 3, "conclusion": "skipped"}])
        self.assertEqual(v.check(ref(step="other")).status, "skipped")

    def test_job_missing_and_no_run(self) -> None:
        v, _ = self.verifier([], name="Other")
        self.assertEqual(v.check(ref()).status, "job-missing")
        self.assertEqual(Verifier(FakeApi({}), "o", {}).check(ref()).status, "no-run")

    def test_job_missing_on_main_falls_back_to_any_branch(self) -> None:
        api = FakeApi(
            {
                "repos/o/r/actions/workflows/ci.yml/runs?status=success&per_page=1&branch": self.RUN,
                "repos/o/r/actions/workflows/ci.yml/runs": {"workflow_runs": [{"id": 8, "html_url": "https://run/8"}]},
                "repos/o/r/actions/runs/7/jobs": {"jobs": [{"name": "Old", "steps": []}]},
                "repos/o/r/actions/runs/8/jobs": {
                    "jobs": [{"name": "Test", "html_url": "j8", "steps": [{"name": "Unit", "conclusion": "success"}]}]
                },
            }
        )
        c = Verifier(api, "o", {"r": "main"}).check(ref())
        self.assertEqual((c.status, c.source, c.run_url), ("success", "non-default-branch", "https://run/8"))

    def test_job_matches(self) -> None:
        self.assertTrue(job_matches("ci / build (ubuntu, 20)", ("ci", "build")))
        self.assertTrue(job_matches("ci / build-x", ("ci", f"build-{WILD}")))
        self.assertFalse(job_matches("lint", ("build",)))


class MatrixTest(Fixture):
    def test_states_and_render(self) -> None:
        units, w, _ = self.walk()
        by = {u.key: u for u in units}
        checks = {id(e): Check("success" if e.ref.step == "Unit" else "skipped") for e in w.evidence}
        m = audit.build_matrix(by, w.evidence, checks, "o")
        self.assertEqual(m["r:node:packages/a"]["test"].state, "yes")
        self.assertEqual(m["r:docker:svc/Dockerfile"]["build"].state, "manual")
        self.assertEqual(m["r:python:research"]["test"].state, "no")
        self.assertEqual(m["r:node:packages/b"]["e2e"].state, "n/a")
        self.assertIn("PR/push", audit.recommend(by["r:docker:svc/Dockerfile"], "build", m["r:docker:svc/Dockerfile"]["build"]))

    def test_opt_outs(self) -> None:
        units, w, _ = self.walk()
        by = {u.key: u for u in units}
        write(
            self.root,
            {
                ".github/workflows/z.yml": "# docker build opt-out: ./svc/Dockerfile -- built by deploy only\n"
                "# docker lint opt-out: gone/Dockerfile -- removed\n"
            },
        )
        rec = audit.opt_outs(self.root, "r")
        self.assertEqual(rec[("r:docker:svc/Dockerfile", "build")], "opt-out (z.yml): built by deploy only")
        m = audit.build_matrix(by, w.evidence, None, "o")
        stale = audit.apply_opt_outs(m, rec)
        self.assertEqual(m["r:docker:svc/Dockerfile"]["build"].state, "opt-out")
        self.assertIn("built by deploy only", m["r:docker:svc/Dockerfile"]["build"].reason)
        self.assertEqual(stale, ["opt-out names no unit/category: r:docker:gone/Dockerfile lint"])
        self.assertEqual(audit.opt_outs(self.root / "nope", "r"), {})

    def test_file_opt_outs(self) -> None:
        units, w, _ = self.walk()
        by = {u.key: u for u in units}
        f = self.root / "optouts.json"
        f.write_text(
            json.dumps(
                [
                    {"repo": "r", "unit": "packages/*", "category": "e2e", "reason": "no browser"},
                    {"repo": "r", "unit": "gone/*", "category": "test", "reason": "x"},
                ]
            )
        )
        rec, stale = audit.file_opt_outs(f, by)
        self.assertEqual(rec[("r:node:packages/a", "e2e")], "opt-out (optouts.json): no browser")
        self.assertEqual(stale, ["opt-out names no unit: r:gone/* test"])
        self.assertEqual(audit.file_opt_outs(self.root / "nope.json", by), ({}, []))
        m = audit.build_matrix(by, w.evidence, None, "o")
        audit.apply_opt_outs(m, rec)
        cell = m["r:node:packages/a"]["e2e"]
        self.assertEqual((cell.state, cell.reason), ("opt-out", "opt-out (optouts.json): no browser"))

    def test_opt_out_never_hides_a_yes(self) -> None:
        units, w, _ = self.walk()
        u = next(x for x in units if x.key == "r:node:packages/a")
        m = audit.build_matrix({u.key: u}, w.evidence, None, "o")
        self.assertEqual(m[u.key]["test"].state, "yes")
        audit.apply_opt_outs(m, {(u.key, "test"): "x"})
        self.assertEqual(m[u.key]["test"].state, "yes")

    def test_dynamic_never_yes(self) -> None:
        units, _, _ = self.walk()
        u = next(x for x in units if x.key == "r:python:research")
        e = Evidence(Hit(u.key, "lint", "turbo {dynamic}"), ref())
        m = audit.build_matrix({u.key: u}, [e], {id(e): Check("success")}, "o")
        self.assertEqual(m[u.key]["lint"].state, "unverified")

    def test_main_end_to_end(self) -> None:
        repos = Path(self.tmp.name) / "repos.txt"
        repos.write_text("r\n")
        (self.root / ".ciaudit.json").write_text(json.dumps({"default_branch": "main"}))
        out, page = Path(self.tmp.name) / "o.json", Path(self.tmp.name) / "o.html"
        rc = audit.main(
            [
                "--snap-prefix",
                str(Path(self.tmp.name) / "stage-"),
                "--repos-file",
                str(repos),
                "--out-json",
                str(out),
                "--out-html",
                str(page),
            ]
        )
        self.assertEqual(rc, 0)
        rep = json.loads(out.read_text())
        self.assertTrue(any(u["key"] == "r:node:packages/a" for u in rep["units"]))
        self.assertIn("<title>", page.read_text())
        self.assertIn("packages/a", render(rep))


class FetchTest(unittest.TestCase):
    def test_extract_strips_prefix_and_skips_links(self) -> None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            data = b"hi"
            ti = tarfile.TarInfo("org-repo-abc/a/f.txt")
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
            ln = tarfile.TarInfo("org-repo-abc/link")
            ln.type = tarfile.SYMTYPE
            ln.linkname = "/etc/passwd"
            tf.addfile(ln)
        with tempfile.TemporaryDirectory() as d:
            fetch.extract_tarball(buf.getvalue(), Path(d))
            self.assertEqual((Path(d) / "a/f.txt").read_text(), "hi")
            self.assertFalse((Path(d) / "link").exists())


if __name__ == "__main__":
    unittest.main()
