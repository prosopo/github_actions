"""Static interpretation of shell commands found in workflow steps.

Given a command string and a working directory inside a snapshot, work out which
units it builds / lints / typechecks / tests / e2e-tests. It follows package
scripts, turbo task graphs (dependsOn), pnpm/turbo filters, Makefile targets and
repo-local shell scripts, a few levels deep. It never executes anything.

Known approximations (stated in the report): turbo cache hits are not
distinguishable from runs; shell control flow (if/case) is flattened, so a
command in any branch counts; `[git-ref]` filters are treated as "all matching".
"""

from __future__ import annotations

import fnmatch
import posixpath
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from units import Unit, in_workspace, load_jsonc, pnpm_workspace

PNPM_BUILTINS = {
    "install",
    "i",
    "add",
    "remove",
    "rm",
    "update",
    "up",
    "store",
    "prune",
    "publish",
    "pack",
    "list",
    "ls",
    "why",
    "config",
    "setup",
    "env",
    "fetch",
    "rebuild",
    "deploy",
    "audit",
    "outdated",
    "link",
    "unlink",
    "import",
    "patch",
    "patch-commit",
    "licenses",
    "approve-builds",
    "init",
    "root",
    "bin",
    "version",
    "dedupe",
    "self-update",
    "cache",
    "--version",
    "-v",
}
NPM_SKIP = {
    "ci",
    "install",
    "i",
    "publish",
    "pack",
    "audit",
    "version",
    "config",
    "cache",
    "set-script",
    "pkg",
    "view",
    "whoami",
    "login",
    "--version",
    "-v",
}
WRAPPERS = {"timeout", "sudo", "cross-env", "env", "nice", "time", "xvfb-run", "dotenv", "stdbuf", "nohup", "exec", "command"}
SCRIPT_EXTS = (".sh", ".bash")


def name_categories(script: str) -> set[str]:
    """Categories implied by an npm script *name*; body analysis adds to these."""
    s = script.lower()
    if re.search(r"(^|:)(lint|format)-?fix|:fix$|:write$|watch|^dev|^start|^serve|preview|clean|install", s):
        return set()
    if re.search(r"e2e|cypress|playwright", s):
        return {"e2e"}
    if re.match(r"test", s):
        return {"test"}
    if re.search(r"typecheck|type-check|check:types|types:check|^tsc$", s):
        return {"typecheck"}
    if re.match(r"build:tsc|build:types", s):
        return {"typecheck", "build"}
    if re.match(r"(build|bundle|compile)", s):
        return {"build"}
    if re.match(r"(lint|format:check|fmt:check|check:format|biome)", s):
        return {"lint"}
    return set()


@dataclass(frozen=True)
class Hit:
    unit: str  # Unit.key
    category: str
    via: str  # human-readable chain, e.g. "turbo run test -> vitest"


@dataclass
class Ctx:
    root: Path
    units: list[Unit]
    hits: list[Hit] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.ws_globs = pnpm_workspace(self.root)
        self.by_snap = {(u.kind, u.meta["snap_path"]): u for u in self.units}
        self.node = [u for u in self.units if u.kind == "node"]
        self.workspace = [u for u in self.node if self.ws_globs is not None and in_workspace(str(u.meta["snap_path"]), self.ws_globs)] or [
            u for u in self.node if u.meta["snap_path"] == "."
        ]
        self.ws_names = {u.name: u for u in self.workspace}
        self.turbo = self._turbo(Path("."))
        self.biome_excludes = self._biome_excludes()

    # ---- lookups -------------------------------------------------------
    def _turbo(self, rel: Path) -> dict[str, dict]:
        f = self.root / rel / "turbo.json"
        if not f.exists():
            return {}
        try:
            doc = load_jsonc(f.read_text())
        except ValueError:
            return {}
        assert isinstance(doc, dict)
        return dict(doc.get("tasks") or doc.get("pipeline") or {})

    def turbo_task(self, pkg: Unit, task: str) -> dict:
        own = self._turbo(Path(str(pkg.meta["snap_path"])))
        if task in own:
            return own[task]
        key = f"{pkg.name}#{task}"
        return self.turbo.get(key) or self.turbo.get(task) or {}

    def _biome_excludes(self) -> list[str]:
        for n in ("biome.json", "biome.jsonc"):
            f = self.root / n
            if f.exists():
                try:
                    doc = load_jsonc(f.read_text())
                except ValueError:
                    return []
                files = (doc or {}).get("files") or {}  # type: ignore[union-attr]
                ex = [g[1:] for g in files.get("includes", []) if g.startswith("!")]
                ex += list(files.get("ignore", []))
                return [g.lstrip("./").rstrip("/") for g in ex]
        return []

    def unit_at(self, rel: str, kinds: tuple[str, ...]) -> Unit | None:
        """Nearest unit of one of `kinds` containing snapshot path `rel`."""
        p = rel
        while True:
            for k in kinds:
                u = self.by_snap.get((k, p))
                if u:
                    return u
            if p == ".":
                return None
            p = posixpath.dirname(p) or "."

    def units_under(self, rel: str, kinds: tuple[str, ...]) -> list[Unit]:
        out = [u for u in self.units if u.kind in kinds and _under(str(u.meta["snap_path"]), rel)]
        if not out:
            u = self.unit_at(rel, kinds)
            out = [u] if u else []
        return out

    def ws_deps(self, pkg: Unit) -> list[Unit]:
        return [self.ws_names[d] for d in sorted(pkg.deps) if d in self.ws_names]

    def ws_dependents(self, pkg: Unit) -> list[Unit]:
        return [u for u in self.workspace if pkg.name in u.deps]

    def emit(self, unit: Unit, cats: set[str], via: str) -> None:
        for c in cats:
            self.hits.append(Hit(unit.key, c, via))


def _under(path: str, base: str) -> bool:
    return base == "." or path == base or path.startswith(base.rstrip("/") + "/")


def norm(cwd: str, p: str) -> str:
    p = p.strip().strip("'\"")
    if p.startswith("${{") or p.startswith("$"):
        return cwd
    out = posixpath.normpath(posixpath.join(cwd, p)) if not p.startswith("/") else posixpath.normpath(p.lstrip("/"))
    return "." if out in ("", ".") else out


# ---- shell splitting ---------------------------------------------------
def segments(text: str) -> list[str]:
    """Split a shell script into simple commands (flattening control flow)."""
    text = re.sub(r"\\\n", " ", text)
    out: list[str] = []
    # Command substitutions run too: lift each innermost $(...) out, leave a token.
    subst = re.compile(r"\$\(([^()]*)\)")
    while (m := subst.search(text)) is not None:
        out += segments(m.group(1))
        text = text[: m.start()] + "SUBST" + text[m.end() :]
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cur, q, i = [], None, 0
        while i < len(line):
            c = line[i]
            if q:
                cur.append(c)
                if c == q:
                    q = None
                elif c == "\\" and q == '"' and i + 1 < len(line):
                    cur.append(line[i + 1])
                    i += 1
            elif c in "'\"":
                q = c
                cur.append(c)
            elif c == "#" and (not cur or cur[-1].isspace()):
                break
            elif c in ";|&()":
                out.append("".join(cur))
                cur = []
            else:
                cur.append(c)
            i += 1
        out.append("".join(cur))
    keep = []
    for s in out:
        s = s.strip()
        s = re.sub(r"^(then|else|do|elif|if|while|until|!|\{|\}|fi|done|esac)\s+", "", s)
        if s and s not in ("then", "else", "fi", "do", "done", "esac", "{", "}"):
            keep.append(s)
    return keep


def tokens(seg: str) -> list[str]:
    try:
        return shlex.split(seg)
    except ValueError:
        return seg.split()


def strip_wrappers(t: list[str]) -> list[str]:
    while t:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t[0]):
            t = t[1:]
        elif t[0] in WRAPPERS:
            t = t[1:]
            while t and (t[0].startswith("-") or re.match(r"^\d+[smhd]?$|^\"?\$", t[0])):
                t = t[1:]
        else:
            break
    return t


# ---- interpreter -------------------------------------------------------
FUNC_START = re.compile(r"^\s*(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\)\s*\{\s*$")


def split_functions(text: str) -> tuple[str, dict[str, str]]:
    """Separate shell function bodies (`name() {` .. `}` at column 0) from top-level code."""
    main: list[str] = []
    bodies: dict[str, str] = {}
    name: str | None = None
    cur: list[str] = []
    for line in text.splitlines():
        m = FUNC_START.match(line) if name is None else None
        if m:
            name, cur = m.group(1), []
        elif name is not None and line.rstrip() == "}":
            bodies[name] = "\n".join(cur)
            name = None
        elif name is not None:
            cur.append(line)
        else:
            main.append(line)
    if name is not None:
        main += cur
    return "\n".join(main), bodies


class Interp:
    def __init__(self, ctx: Ctx, max_depth: int = 6):
        self.ctx = ctx
        self.max_depth = max_depth
        self.funcs: dict[str, str] = {}  # shell functions of the script being interpreted
        self.called: set[str] = set()

    def run(self, text: str, cwd: str, via: str = "", depth: int = 0) -> str:
        """Interpret shell text; returns the cwd after its top-level `cd`s."""
        if depth > self.max_depth:
            return cwd
        for seg in segments(text):
            t = strip_wrappers(tokens(seg))
            if not t:
                continue
            if t[0] == "cd" and len(t) > 1:
                cwd = self.cd_target(cwd, t[1])
                continue
            if t[0] in self.funcs and t[0] not in self.called:
                self.called.add(t[0])
                cwd = self.run(self.funcs[t[0]], cwd, via, depth + 1)
                continue
            self.command(t, cwd, via, depth)
        return cwd

    def cd_target(self, cwd: str, arg: str) -> str:
        """`cd "$x"/../tests`: keep the literal tail when it names a real directory."""
        if "$" not in arg and "SUBST" not in arg:
            return norm(cwd, arg)
        tail = re.split(r"\$\{?\w+\}?|SUBST", arg)[-1].strip("/")
        while tail.startswith("../"):
            tail = tail[3:]
        for cand in (norm(cwd, tail), norm(".", tail)) if tail and tail != ".." else ():
            if (self.ctx.root / cand).is_dir():
                return cand
        return cwd

    def command(self, t: list[str], cwd: str, via: str, depth: int) -> None:
        exe = posixpath.basename(t[0])
        v = f"{via} -> {' '.join(t[:4])}" if via else " ".join(t[:6])
        if exe == "eval":
            return self.run(" ".join(t[1:]), cwd, via, depth + 1)
        if exe in ("pnpm", "pnpx"):
            return self.pnpm(t[1:], cwd, v, depth)
        if exe in ("npm", "yarn"):
            return self.npm(t[1:], cwd, v, depth)
        if exe == "npx":
            rest = [x for x in t[1:] if not x.startswith("-")]
            return self.command(rest, cwd, v, depth) if rest else None
        if exe == "turbo":
            return self.turbo(t[1:], cwd, v, depth)
        if exe in ("concurrently", "run-s", "run-p", "npm-run-all"):
            return self.multi(exe, t[1:], cwd, v, depth)
        if exe in ("bash", "sh", "zsh") and len(t) > 1:
            if t[1] == "-c" and len(t) > 2:
                return self.run(t[2], cwd, v, depth + 1)
            return self.script(t[1], cwd, v, depth)
        if exe.endswith(SCRIPT_EXTS) or t[0].startswith("./"):
            return self.script(t[0], cwd, v, depth)
        if exe in ("python", "python3", "uv", "uvx", "poetry", "pipx"):
            return self.python(t, cwd, v, depth)
        if exe == "php" and len(t) > 1 and "vendor/bin/" in t[1]:
            return self.command(t[1:], cwd, v, depth)
        if exe in ("make", "just"):
            return self.make(t, cwd, v, depth)
        if exe == "cargo":
            return self.cargo(t[1:], cwd, v)
        if exe == "go":
            return self.go(t[1:], cwd, v)
        if exe in ("docker", "podman"):
            return self.docker(t[1:], cwd, v)
        self.tool(t, cwd, v)

    # -- JS ---------------------------------------------------------------
    def pnpm(self, a: list[str], cwd: str, v: str, depth: int) -> None:
        filters: list[str] = []
        recursive = False
        dirflag = cwd
        rest: list[str] = []
        i = 0
        while i < len(a):
            x = a[i]
            if x in ("--filter", "-F") and i + 1 < len(a):
                filters.append(a[i + 1])
                i += 2
                continue
            if x.startswith("--filter="):
                filters.append(x.split("=", 1)[1])
                i += 1
                continue
            if x in ("-r", "--recursive"):
                recursive = True
                i += 1
                continue
            if x in ("-C", "--dir") and i + 1 < len(a):
                dirflag = norm(cwd, a[i + 1])
                i += 2
                continue
            if x.startswith("--") and not rest:
                i += 1
                continue
            if x in ("-w", "--workspace-root") and not rest:
                dirflag = "."
                i += 1
                continue
            if x.startswith("-") and not rest:
                i += 1
                continue
            rest.append(x)
            i += 1
        if not rest:
            return
        cmd, args = rest[0], rest[1:]
        if any("$" in f for f in filters):
            v += " {dynamic}"
        if cmd in PNPM_BUILTINS:
            return
        if cmd in ("exec", "dlx"):
            for pkg_dir in self.targets(filters, recursive, dirflag):
                self.command(strip_wrappers([x for x in args if x != "--"]), pkg_dir, v, depth + 1)
            return
        if cmd == "turbo":
            return self.turbo(args, dirflag, v, depth)
        if cmd in ("run", "run-script"):
            args = [x for x in args if not x.startswith("-")] or args
            if "-r" in rest or "--recursive" in rest:
                recursive = True
            if not args:
                return
            cmd, args = args[0], args[1:]
        for pkg_dir in self.targets(filters, recursive, dirflag):
            self.run_script(pkg_dir, cmd, args, v, depth, binary_fallback=True)

    def npm(self, a: list[str], cwd: str, v: str, depth: int) -> None:
        prefix, ws, rest = cwd, [], []
        all_ws = with_root = False
        i = 0
        while i < len(a):
            x = a[i]
            if x in ("--prefix", "-C") and i + 1 < len(a):
                prefix = norm(cwd, a[i + 1])
                i += 2
                continue
            if x in ("-w", "--workspace") and i + 1 < len(a):
                ws.append(a[i + 1])
                i += 2
                continue
            if x in ("--workspaces", "-ws"):
                all_ws = True
                i += 1
                continue
            if x == "--include-workspace-root":
                with_root = True
                i += 1
                continue
            if x.startswith("-") and not rest:
                i += 1
                continue
            rest.append(x)
            i += 1
        if not rest or rest[0] in NPM_SKIP:
            return
        cmd = rest[0]
        if cmd in ("exec", "x"):
            return self.command([x for x in rest[1:] if x != "--" and not x.startswith("-")], prefix, v, depth + 1)
        if cmd in ("test", "t", "tst"):
            script = "test"
        elif cmd in ("run", "run-script", "rum", "urn") and len(rest) > 1:
            script = rest[1]
        elif cmd in ("start", "stop", "restart"):
            return
        else:
            script = cmd  # `yarn build`
        dirs = [self.ctx.ws_names[w].meta["snap_path"] if w in self.ctx.ws_names else norm(prefix, w) for w in ws] or [prefix]
        if all_ws:
            # npm --workspaces runs in every member and skips the root unless asked; a missing
            # script is an error without --if-present, so crediting only units that define it is exact.
            dirs = [str(u.meta["snap_path"]) for u in self.ctx.workspace if u.meta["snap_path"] != "."] + (["."] if with_root else [])
        for d in dirs:
            self.run_script(str(d), script, [], v, depth, binary_fallback=False)

    def targets(self, filters: list[str], recursive: bool, cwd: str) -> list[str]:
        c = self.ctx
        if filters:
            return [str(u.meta["snap_path"]) for u in select(c, filters, cwd)]
        if recursive:
            return [str(u.meta["snap_path"]) for u in c.workspace if _under(str(u.meta["snap_path"]), cwd)]
        u = c.unit_at(cwd, ("node",))
        return [str(u.meta["snap_path"])] if u else [cwd]

    def run_script(self, pkg_dir: str, script: str, args: list[str], v: str, depth: int, binary_fallback: bool) -> None:
        c = self.ctx
        u = c.unit_at(pkg_dir, ("node",))
        if u and script in u.scripts:
            c.emit(u, name_categories(script), v)
            for hook in (f"pre{script}", f"post{script}"):
                if hook in u.scripts:
                    self.run(u.scripts[hook], str(u.meta["snap_path"]), v, depth + 1)
            body = u.scripts[script] + (" " + " ".join(shlex.quote(x) for x in args) if args else "")
            self.run(body, str(u.meta["snap_path"]), f"{v} [{u.name}:{script}]", depth + 1)
        elif binary_fallback:
            self.command([script, *args], pkg_dir, v, depth + 1)

    def turbo(self, a: list[str], cwd: str, v: str, depth: int) -> None:
        c = self.ctx
        if a and a[0] == "run":
            a = a[1:]
        tasks, filters = [], []
        i = 0
        while i < len(a):
            x = a[i]
            if x == "--":
                break
            if x in ("--filter", "-F") and i + 1 < len(a):
                filters.append(a[i + 1])
                i += 2
                continue
            if x.startswith(("--filter=", "-F=")):
                filters.append(x.split("=", 1)[1])
                i += 1
                continue
            if x.startswith("-"):
                if (
                    "=" not in x
                    and i + 1 < len(a)
                    and x in ("--concurrency", "--cache-dir", "--output-logs", "--log-order", "--cache", "--env-mode", "--ui")
                ):
                    i += 1
                i += 1
                continue
            if "$" in x:  # e.g. "${filters[@]}": a filter list built at runtime
                filters.append("*")
                i += 1
                continue
            tasks.append(x)
            i += 1
        if any("$" in f or f == "*" for f in filters):
            v += " {dynamic}"
        if filters:
            pkgs = select(c, filters, ".")
        else:
            here = c.unit_at(cwd, ("node",))
            pkgs = c.workspace if cwd == "." or not here or here.meta["snap_path"] == "." else [here]
        seen: set[tuple[str, str]] = set()
        for p in pkgs:
            for t in tasks:
                self._turbo_task(p, t, f"{v} [turbo {t}]", depth, seen)

    def _turbo_task(self, pkg: Unit, task: str, v: str, depth: int, seen: set[tuple[str, str]]) -> None:
        if (pkg.key, task) in seen:
            return
        seen.add((pkg.key, task))
        c = self.ctx
        for dep in c.turbo_task(pkg, task).get("dependsOn", []):
            if dep.startswith("^"):
                for d in c.ws_deps(pkg):
                    self._turbo_task(d, dep[1:], v, depth, seen)
            elif "#" in dep:
                name, t2 = dep.split("#", 1)
                if name in c.ws_names:
                    self._turbo_task(c.ws_names[name], t2, v, depth, seen)
            elif not dep.startswith("$"):
                self._turbo_task(pkg, dep, v, depth, seen)
        if task in pkg.scripts:
            self.run_script(str(pkg.meta["snap_path"]), task, [], v, depth, binary_fallback=False)

    def multi(self, exe: str, a: list[str], cwd: str, v: str, depth: int) -> None:
        pos = [x for x in a if not x.startswith("-")]
        if exe == "concurrently":
            for p in pos:
                if p not in ("first", "last", "all") and not p.isdigit():
                    self.run(p, cwd, v, depth + 1)
            return
        u = self.ctx.unit_at(cwd, ("node",))
        if not u:
            return
        for pat in pos:
            for s in u.scripts:
                if fnmatch.fnmatch(s, pat):
                    self.run_script(str(u.meta["snap_path"]), s, [], v, depth, binary_fallback=False)

    # -- files ------------------------------------------------------------
    def script(self, path: str, cwd: str, v: str, depth: int) -> None:
        rel = norm(cwd, path.replace("${{ github.workspace }}/", "/").replace("$GITHUB_WORKSPACE/", "/"))
        f = self.ctx.root / rel
        if f.is_file() and f.stat().st_size < 200_000:
            try:
                text = f.read_text()
            except UnicodeDecodeError:
                return
            # Scripts run from the caller's cwd unless they cd themselves. Function
            # bodies are called after the top-level code, so they see its final cwd.
            # Shell functions are inlined at their call sites so a `cd` in one
            # function is seen by the functions it calls; uncalled ones run last.
            main, bodies = split_functions(text)
            outer, self.funcs, self.called = (self.funcs, self.called), {**self.funcs, **bodies}, set()
            end = self.run(main, cwd, f"{v} [{rel}]", depth + 1)
            for name in [n for n in bodies if n not in self.called]:
                self.run(bodies[name], end, f"{v} [{rel}]", depth + 1)
            self.funcs, self.called = outer
        else:
            self.tool([posixpath.basename(path)], cwd, v)

    def make(self, t: list[str], cwd: str, v: str, depth: int) -> None:
        d, targets = cwd, []
        i = 1
        while i < len(t):
            if t[i] == "-C" and i + 1 < len(t):
                d = norm(cwd, t[i + 1])
                i += 2
                continue
            if not t[i].startswith("-") and "=" not in t[i]:
                targets.append(t[i])
            i += 1
        f = next(
            (self.ctx.root / d / n for n in ("Makefile", "makefile", "GNUmakefile", "justfile") if (self.ctx.root / d / n).exists()), None
        )
        if not f:
            return
        rules = parse_make(f.read_text(errors="replace"))
        for tg in targets or list(rules)[:1]:
            for line in rules.get(tg, []):
                self.run(line.lstrip("@-"), d, f"{v} [make {tg}]", depth + 1)

    # -- other ecosystems --------------------------------------------------
    def python(self, t: list[str], cwd: str, v: str, depth: int) -> None:
        exe = posixpath.basename(t[0])
        if exe in ("uv", "poetry", "pipx") and len(t) > 1 and t[1] in ("run", "tool"):
            rest = t[2:]
            if rest and rest[0] == "run":
                rest = rest[1:]
            rest = skip_launcher_opts(rest)
            return self.command(rest, cwd, v, depth + 1) if rest else None
        if exe == "uvx":
            rest = skip_launcher_opts(t[1:])
            if rest:
                rest = [rest[0].split("@", 1)[0], *rest[1:]]  # `uvx ruff@0.17.2` pins a version
            return self.command(rest, cwd, v, depth + 1) if rest else None
        a = t[1:]
        if "-m" in a:
            mod = a[a.index("-m") + 1] if a.index("-m") + 1 < len(a) else ""
            args = a[a.index("-m") + 2 :]
            if mod in ("pytest", "unittest"):
                return self.python_tests(mod, args, cwd, v)
            if mod in ("mypy", "pyright"):
                return self.emit_tree(cwd, ("python",), {"typecheck"}, v)
            if mod in ("ruff", "flake8", "pylint", "black"):
                return self.emit_tree(cwd, ("python",), {"lint"}, v)
            if mod in ("compileall", "py_compile"):
                return self.emit_tree(cwd, ("python",), {"build"}, v)
            return
        scripts = [x for x in a if x.endswith(".py")]
        if scripts and posixpath.basename(scripts[0]) == "workflow_lint.py":
            # prosopo/github_actions workflow-lint action: the shared workflow linter
            u = next((x for x in self.ctx.units if x.kind == "workflows" and x.meta["snap_path"] == ".github/workflows"), None)
            if u:
                self.ctx.emit(u, {"lint"}, v)
            return
        if scripts and posixpath.basename(scripts[0]) == "run_python_tests.py":
            return self.python_test_runner(a, cwd, v)
        if scripts and re.search(r"(^|/)test_[^/]*\.py$|_test\.py$", scripts[0]):
            self.emit_tree(posixpath.dirname(norm(cwd, scripts[0])) or ".", ("python",), {"test"}, v)

    def python_test_runner(self, a: list[str], cwd: str, v: str) -> None:
        """prosopo/github_actions run_python_tests.py: `unittest discover` in every dir under --root holding
        test_*.py, skipping fixtures; only units with such a test file are credited."""
        root = norm(cwd, a[a.index("--root") + 1]) if "--root" in a and a.index("--root") + 1 < len(a) else norm(cwd, ".github")
        for u in self.ctx.units_under(root, ("python",)):
            d = self.ctx.root / str(u.meta["snap_path"])
            if "fixtures" not in Path(str(u.meta["snap_path"])).parts and any(d.rglob("test_*.py")):
                self.ctx.emit(u, {"test"}, v)

    def python_tests(self, mod: str, args: list[str], cwd: str, v: str) -> None:
        """`unittest discover -s D` / bare `pytest` cover the tree; named modules or paths only their own unit."""
        pos = [x for i, x in enumerate(args) if not x.startswith("-") and not (i and args[i - 1] in PYTEST_VALUE_OPTS)]
        if mod == "unittest" and (not pos or pos[0] == "discover"):
            start = cwd
            if "-s" in args and args.index("-s") + 1 < len(args):
                start = norm(cwd, args[args.index("-s") + 1])
            return self.emit_tree(start, ("python",), {"test"}, v)
        if "--collect-only" in args or "--co" in args:
            return  # imports tests without running them
        if any("$" in p for p in pos):
            return self.emit_tree(cwd, ("python",), {"test"}, v + " {dynamic}")
        if mod == "pytest" and not pos:
            paths = pytest_testpaths(self.ctx.root / cwd)
            if not paths:
                return self.emit_tree(cwd, ("python",), {"test"}, v)
            pos = paths
        dirs = {cwd} if mod == "unittest" else {norm(cwd, p.split("::")[0]) for p in pos}
        for d in sorted(dirs):
            u = self.ctx.unit_at(d, ("python",))
            if u:
                self.ctx.emit(u, {"test"}, v)
            else:
                self.ctx.unresolved.append(f"{v} @ {d}")

    def emit_tree(self, rel: str, kinds: tuple[str, ...], cats: set[str], v: str) -> None:
        us = self.ctx.units_under(rel, kinds)
        if not us:
            self.ctx.unresolved.append(f"{v} @ {rel}")
        for u in us:
            self.ctx.emit(u, cats, v)

    def cargo(self, a: list[str], cwd: str, v: str) -> None:
        a = [x for x in a if not x.startswith("+")]
        if not a:
            return
        sub = a[0]
        if sub == "llvm-cov":
            sub = "test" if not (len(a) > 1 and a[1] == "report") else ""
        elif sub == "nextest":
            sub = "test"
        cats = {
            "build": {"build"},
            "test": {"test", "build"},
            "clippy": {"lint"},
            "fmt": {"lint"},
            "check": {"typecheck"},
            "doc": {"build"},
        }.get(sub, set())
        if not cats:
            return
        d = cwd
        pkgs: list[str] = []
        workspace = False
        for i, x in enumerate(a):
            if x in ("-p", "--package") and i + 1 < len(a):
                pkgs.append(a[i + 1])
            elif x.startswith("--package="):
                pkgs.append(x.split("=", 1)[1])
            elif x in ("--workspace", "--all"):
                workspace = True
            elif x == "--manifest-path" and i + 1 < len(a):
                d = posixpath.dirname(norm(cwd, a[i + 1])) or "."
        crates = self.ctx.units_under(d, ("rust",))
        if pkgs:
            crates = [u for u in self.ctx.units if u.kind == "rust" and u.name in pkgs]
        elif not workspace:
            own = self.ctx.by_snap.get(("rust", d))
            if own and not _is_workspace_root(self.ctx.root / d):
                crates = [own]
        if not crates:
            self.ctx.unresolved.append(f"{v} @ {d}")
        for u in crates:
            self.ctx.emit(u, cats, v)

    def go(self, a: list[str], cwd: str, v: str) -> None:
        cats = {"build": {"build"}, "test": {"test"}, "vet": {"lint", "typecheck"}}.get(a[0] if a else "", set())
        if cats:
            self.emit_tree(cwd, ("go",), cats, v)

    def docker(self, a: list[str], cwd: str, v: str) -> None:
        if a and a[0] in ("buildx", "image"):
            a = a[1:]
        if not a:
            return
        if a[0] == "compose" or a[0] == "compose-build":
            return self.compose(a[1:], cwd, v)
        if a[0] != "build":
            return
        file, pos = None, []
        i = 1
        while i < len(a):
            x = a[i]
            if x in ("-f", "--file") and i + 1 < len(a):
                file = a[i + 1]
                i += 2
                continue
            if x.startswith("--file="):
                file = x.split("=", 1)[1]
                i += 1
                continue
            if x.startswith("-"):
                if (
                    "=" not in x
                    and i + 1 < len(a)
                    and x
                    in (
                        "-t",
                        "--tag",
                        "--build-arg",
                        "--target",
                        "--platform",
                        "--cache-from",
                        "--cache-to",
                        "--secret",
                        "--label",
                        "--network",
                        "--progress",
                        "-o",
                        "--output",
                        "--build-context",
                    )
                ):
                    i += 1
                i += 1
                continue
            pos.append(x)
            i += 1
        ctxdir = norm(cwd, pos[-1]) if pos else cwd
        path = norm(cwd, file) if file else norm(ctxdir, "Dockerfile")
        self.emit_docker(path, v)

    def emit_docker(self, path: str, v: str) -> None:
        path = posixpath.normpath(path)
        u = self.ctx.by_snap.get(("docker", path))
        if u:
            self.ctx.emit(u, {"build"}, v)
        else:
            self.ctx.unresolved.append(f"{v} @ docker:{path}")

    def compose(self, a: list[str], cwd: str, v: str) -> None:
        files = [norm(cwd, a[i + 1]) for i, x in enumerate(a) if x in ("-f", "--file") and i + 1 < len(a)]
        if not ("build" in a or "--build" in a):
            return
        if not files:
            files = [
                norm(cwd, n)
                for n in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
                if (self.ctx.root / norm(cwd, n)).exists()
            ]
        import yaml  # local: only compose needs it here

        for f in files:
            p = self.ctx.root / f
            if not p.exists():
                self.ctx.unresolved.append(f"{v} @ compose:{f}")
                continue
            try:
                doc = yaml.safe_load(p.read_text()) or {}
            except yaml.YAMLError:
                continue
            base = posixpath.dirname(f) or "."
            for svc in (doc.get("services") or {}).values():
                b = (svc or {}).get("build")
                if not b:
                    continue
                ctxd = norm(base, b if isinstance(b, str) else b.get("context", "."))
                df = "Dockerfile" if isinstance(b, str) else b.get("dockerfile", "Dockerfile")
                self.emit_docker(posixpath.join(ctxd, df) if ctxd != "." else df, v)

    def tool(self, t: list[str], cwd: str, v: str) -> None:
        exe = posixpath.basename(t[0])
        args = t[1:]
        sub = args[0] if args else ""
        tree_paths = [norm(cwd, x) for x in args[1:] if not x.startswith("-") and x not in (".",)] if exe == "biome" else []
        if exe == "biome" and sub in ("check", "lint", "ci", "format"):
            if "--write" in args or "--fix" in args:
                return
            if "--changed" in args or "--staged" in args:
                return  # only changed files: no unit is guaranteed coverage
            for base in tree_paths or [cwd]:
                for u in self.ctx.units_under(base, ("node",)):
                    if not any(
                        fnmatch.fnmatch(str(u.meta["snap_path"]), g) or fnmatch.fnmatch(str(u.meta["snap_path"]) + "/", g)
                        for g in self.ctx.biome_excludes
                    ):
                        self.ctx.emit(u, {"lint"}, v)
            return
        if exe in ("eslint", "prettier", "stylelint", "htmlhint"):
            if exe == "prettier" and "--check" not in args:
                return
            if "--fix" in args or "--write" in args:
                return
            paths = [norm(cwd, x) for x in args if not x.startswith("-") and (self.ctx.root / norm(cwd, x)).is_dir()]
            for base in paths or [cwd]:
                for u in self.ctx.units_under(base, ("node",)) if base != cwd else [x for x in [self.ctx.unit_at(cwd, ("node",))] if x]:
                    self.ctx.emit(u, {"lint"}, v)
            return
        simple = {
            "tsc": ({"typecheck"} if "--noEmit" in args else {"typecheck", "build"}, ("node",)),
            "vue-tsc": ({"typecheck"}, ("node",)),
            "svelte-check": ({"typecheck"}, ("node",)),
            "vitest": ({"test"}, ("node",)),
            "jest": ({"test"}, ("node",)),
            "mocha": ({"test"}, ("node",)),
            "ava": ({"test"}, ("node",)),
            "c8": ({"test"}, ("node",)),
            "cypress": ({"e2e"} if sub == "run" else set(), ("node",)),
            "playwright": ({"e2e"} if sub == "test" else set(), ("node",)),
            "wdio": ({"e2e"}, ("node",)),
            "vite": ({"build"} if sub == "build" else set(), ("node",)),
            "webpack": ({"build"}, ("node",)),
            "rollup": ({"build"}, ("node",)),
            "esbuild": ({"build"}, ("node",)),
            "tsup": ({"build"}, ("node",)),
            "next": ({"build"} if sub == "build" else set(), ("node",)),
            "astro": ({"build"} if sub == "build" else {"typecheck"} if sub == "check" else set(), ("node",)),
            "docusaurus": ({"build"} if sub == "build" else set(), ("node",)),
            "pytest": ({"test"}, ("python",)),
            "mypy": ({"typecheck"}, ("python",)),
            "pyright": ({"typecheck"}, ("python",)),
            "ruff": (
                {"lint"} if sub in ("check", "format") and "--fix" not in args and ("--check" in args or sub == "check") else set(),
                ("python",),
            ),
            "flake8": ({"lint"}, ("python",)),
            "pylint": ({"lint"}, ("python",)),
            "black": ({"lint"} if "--check" in args else set(), ("python",)),
            "ansible-lint": ({"lint"}, ("ansible",)),
            "ansible-playbook": ({"typecheck"} if "--syntax-check" in args else set(), ("ansible",)),
            "hadolint": ({"lint"}, ("docker",)),
            "actionlint": ({"lint"}, ("workflows",)),
            "zizmor": ({"lint"}, ("workflows",)),
            "phpunit": ({"test"}, ("php",)),
            "phpcs": ({"lint"}, ("php",)),
            "phpstan": ({"typecheck"}, ("php",)),
            "pest": ({"test"}, ("php",)),
            "composer": ({"lint"} if sub in ("lint", "phpcs") else {"test"} if sub == "test" else set(), ("php",)),
            "golangci-lint": ({"lint"}, ("go",)),
            "swift": ({"build"} if sub == "build" else {"test"} if sub == "test" else set(), ("ios",)),
            "xcodebuild": ({c for c in ("build", "test") if c in args}, ("ios",)),
            "gradlew": (gradle_cats(args), ("android",)),
        }
        simple["gradle"] = simple["gradlew"]
        if exe in simple:
            cats, kinds = simple[exe]
            if not cats:
                return
            if exe == "actionlint":
                u = self.ctx.unit_at(posixpath.join(cwd, ".github/workflows"), ("workflows",)) or next(
                    (x for x in self.ctx.units if x.kind == "workflows" and x.meta["snap_path"] == ".github/workflows"), None
                )
                if u:
                    self.ctx.emit(u, cats, v)
                return
            if exe == "pytest":
                return self.python_tests("pytest", args, cwd, v)
            if exe in ("hadolint",):
                for x in args:
                    if not x.startswith("-"):
                        self.emit_docker(norm(cwd, x), v)
                return
            if kinds == ("node",):
                u = self.ctx.unit_at(cwd, kinds)
                if u:
                    self.ctx.emit(u, cats, v)
                return
            self.emit_tree(cwd, kinds, cats, v)


PYTEST_VALUE_OPTS = {
    "-p",
    "-k",
    "-m",
    "-c",
    "-o",
    "-W",
    "--rootdir",
    "--confcutdir",
    "--basetemp",
    "--ignore",
    "--deselect",
    "--tb",
    "--junitxml",
}
LAUNCHER_VALUE_OPTS = {
    "--python",
    "-p",
    "--with",
    "--from",
    "--project",
    "--directory",
    "--extra",
    "--group",
    "--package",
    "--env-file",
    "--index",
    "--index-url",
    "--with-requirements",
    "--only-group",
}


def skip_launcher_opts(a: list[str]) -> list[str]:
    """Drop uv/uvx/poetry options (and their values) before the command they launch."""
    i = 0
    while i < len(a) and a[i].startswith("-"):
        i += 2 if a[i] in LAUNCHER_VALUE_OPTS and "=" not in a[i] else 1
    return a[i:]


def pytest_testpaths(d: Path) -> list[str]:
    """testpaths from pyproject [tool.pytest.ini_options], or [] when unset."""
    import tomllib

    f = d / "pyproject.toml"
    try:
        doc = tomllib.loads(f.read_text())
    except (OSError, ValueError):
        return []
    return list(((doc.get("tool") or {}).get("pytest") or {}).get("ini_options", {}).get("testpaths") or [])


def gradle_cats(args: list[str]) -> set[str]:
    out: set[str] = set()
    if any(x in args for x in ("build", "assemble", "assembleDebug", "assembleRelease")):
        out.add("build")
    if any("test" in x.lower() for x in args):
        out.add("test")
    if any("lint" in x.lower() or "detekt" in x or "ktlint" in x for x in args):
        out.add("lint")
    return out


def _is_workspace_root(d: Path) -> bool:
    try:
        import tomllib

        return "workspace" in tomllib.loads((d / "Cargo.toml").read_text())
    except (OSError, ValueError):
        return False


def parse_make(text: str) -> dict[str, list[str]]:
    rules: dict[str, list[str]] = {}
    cur: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("\t") and cur is not None:
            cur.append(line.strip())
            continue
        m = re.match(r"^([A-Za-z0-9_.\-/ ]+?)\s*::?\s*([^=].*)?$", line)
        if m and not line.startswith(("#", ".PHONY")):
            cur = []
            for name in m.group(1).split():
                rules[name] = cur
            deps = (m.group(2) or "").split("#")[0].split()
            for d in deps:
                cur.append(f"make {d}")
        elif not line.strip() or not line.startswith(" "):
            cur = None if not line.startswith("\t") else cur
    return rules


def select(c: Ctx, filters: list[str], cwd: str) -> list[Unit]:
    """pnpm/turbo --filter semantics over the workspace."""
    inc = [f for f in filters if not f.startswith("!")]
    exc = [f[1:] for f in filters if f.startswith("!")]
    chosen: list[Unit] = []
    for f in inc:
        chosen += _sel(c, f, cwd)
    if not inc:
        chosen = list(c.workspace)
    drop = {u.key for f in exc for u in _sel(c, f, cwd)}
    out, seen = [], set()
    for u in chosen:
        if u.key not in drop and u.key not in seen:
            seen.add(u.key)
            out.append(u)
    return out


def _sel(c: Ctx, f: str, cwd: str) -> list[Unit]:
    f = f.strip("'\"")
    f = re.sub(r"\$\{?[\w@\[\]#]+\}?", "*", f)  # unresolved shell variable: any package
    f = re.sub(r"\[[^\]]*\]", "", f)  # git-ref part: treated as "all matching"
    deps_mode = f.endswith("...") and not f.endswith("^...")
    deps_only = f.endswith("^...")
    dependents = f.startswith("...")
    core = f.removesuffix("^...").removesuffix("...").removeprefix("...")
    if core.startswith("{") and core.endswith("}"):
        core = core[1:-1]
    if not core:
        base = list(c.workspace)
    elif core.startswith(".") or "/" in core and not core.startswith("@"):
        g = norm(cwd, core).rstrip("/")
        base = [
            u
            for u in c.workspace
            if fnmatch.fnmatch(str(u.meta["snap_path"]), g)
            or (g.endswith("/**") and _under(str(u.meta["snap_path"]), g[:-3]))
            or u.meta["snap_path"] == g
        ]
    else:
        base = [u for u in c.workspace if fnmatch.fnmatch(u.name, core)]
    out = [] if deps_only else list(base)
    if deps_mode or deps_only:
        stack = list(base)
        seen = {u.key for u in out}
        while stack:
            for d in c.ws_deps(stack.pop()):
                if d.key not in seen:
                    seen.add(d.key)
                    out.append(d)
                    stack.append(d)
    if dependents:
        stack = list(base)
        seen = {u.key for u in out}
        while stack:
            for d in c.ws_dependents(stack.pop()):
                if d.key not in seen:
                    seen.add(d.key)
                    out.append(d)
                    stack.append(d)
    return out
