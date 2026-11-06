"""Walk GitHub Actions workflows (incl. reusable workflows and composite actions)
and attribute each interpreted command hit to the step that runs it.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from cmds import Ctx, Hit, Interp, norm

CI_TRIGGERS = {"push", "pull_request", "merge_group", "pull_request_target"}
EXPR = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
WILD = "\x00"  # placeholder for an expression we cannot evaluate statically


@dataclass(frozen=True)
class StepRef:
    repo: str  # repo whose workflow run carries the step
    workflow: str  # top-level workflow file name (the one with triggers)
    triggers: tuple[str, ...]
    job: tuple[str, ...]  # display-name chain: caller job / called job
    step: str  # display name pattern (WILD where an expression was)
    index: int  # 0-based index among the job's steps


@dataclass(frozen=True)
class Evidence:
    hit: Hit
    ref: StepRef


def triggers_of(doc: dict) -> tuple[str, ...]:
    on = doc.get("on", doc.get(True))
    if isinstance(on, str):
        return (on,)
    if isinstance(on, list):
        return tuple(on)
    if isinstance(on, dict):
        return tuple(on.keys())
    return ()


def load(path: Path) -> dict:
    try:
        doc = yaml.safe_load(path.read_text())
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


class Scope:
    def __init__(self, inputs: dict[str, str], matrix: dict[str, str], env: dict[str, str]):
        self.inputs, self.matrix, self.env = inputs, matrix, env

    def child(self, env: dict | None = None, matrix: dict | None = None, inputs: dict | None = None) -> Scope:
        e = dict(self.env)
        for k, val in (env or {}).items():
            e[str(k)] = self.sub(str(val))
        return Scope(dict(inputs if inputs is not None else self.inputs), dict(matrix if matrix is not None else self.matrix), e)

    def sub(self, text: str) -> str:
        def rep(m: re.Match[str]) -> str:
            e = m.group(1)
            for prefix, table in (("inputs.", self.inputs), ("matrix.", self.matrix), ("env.", self.env)):
                if e.startswith(prefix) and e[len(prefix) :] in table:
                    return str(table[e[len(prefix) :]])
            if e in ("github.workspace",):
                return "."
            return WILD

        out = EXPR.sub(rep, text)
        for k, val in self.env.items():
            if WILD in val or "$" in val:
                continue
            out = re.sub(rf'"\$\{{?{k}\}}?"|\$\{{{k}\}}|\${k}\b', lambda _m, v=val: v, out)
        return out


class Walker:
    """Interpret every job and step of a repo's workflows against one snapshot context."""

    def __init__(self, ctx: Ctx, repo: str, snapshots: dict[str, Path]):
        self.ctx = ctx
        self.repo = repo
        self.snapshots = snapshots  # repo name -> snapshot root (for remote actions/workflows)
        self.interp = Interp(ctx)
        self.evidence: list[Evidence] = []
        self.notes: list[str] = []

    def resolve_uses(self, uses: str, kind: str) -> Path | None:
        """Map a `uses:` value to a file in a snapshot. kind = 'workflow' or 'action'."""
        u = uses.split("@", 1)[0]
        if u.startswith("./.shared-actions/"):
            base, rest = self.snapshots.get("github_actions"), u[len("./.shared-actions/") :]
        elif u.startswith("./"):
            base, rest = self.ctx.root, u[2:]
        else:
            parts = u.split("/")
            if len(parts) < 3 or parts[0] != "prosopo":
                return None
            base, rest = self.snapshots.get(parts[1]), "/".join(parts[2:])
        if base is None:
            return None
        p = base / rest
        if kind == "action":
            for n in ("action.yml", "action.yaml"):
                if (p / n).exists():
                    return p / n
            return None
        return p if p.exists() else None

    def run_all(self, wf_dir: Path) -> None:
        for f in sorted(wf_dir.glob("*.y*ml")):
            doc = load(f)
            trig = triggers_of(doc)
            if not trig or set(trig) == {"workflow_call"}:
                continue  # reached through its callers, whose runs carry its jobs
            self.workflow(doc, f.name, trig, (), Scope({}, {}, {}), depth=0)

    def workflow(self, doc: dict, top: str, trig: tuple[str, ...], chain: tuple[str, ...], scope: Scope, depth: int) -> None:
        if depth > 4:
            return
        wscope = scope.child(env=doc.get("env") or {})
        for jid, job in (doc.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            for matrix in self.matrices(job, wscope):
                js = wscope.child(env=job.get("env") or {}, matrix=matrix)
                name = js.sub(str(job.get("name", jid)))
                jchain = chain + (name,)
                if "uses" in job:
                    target = self.resolve_uses(str(job["uses"]), "workflow")
                    if target is None:
                        self.notes.append(f"{top}:{jid}: unresolved reusable workflow {job['uses']}")
                        continue
                    called = load(target)
                    inputs = {}
                    decl = (called.get("on") or called.get(True) or {}) or {}
                    decl = (decl.get("workflow_call") or {}) if isinstance(decl, dict) else {}
                    for k, spec in ((decl.get("inputs") or {}) if isinstance(decl, dict) else {}).items():
                        if isinstance(spec, dict) and "default" in spec:
                            inputs[k] = str(spec["default"])
                    for k, val in (job.get("with") or {}).items():
                        inputs[k] = js.sub(str(val))
                    self.workflow(called, top, trig, jchain, Scope(inputs, {}, {}), depth + 1)
                    continue
                cwd_default = js.sub(str(((job.get("defaults") or {}).get("run") or {}).get("working-directory", ".")))
                for idx, st in enumerate(job.get("steps") or []):
                    if not isinstance(st, dict):
                        continue
                    ref = StepRef(self.repo, top, trig, jchain, self.step_name(st, js), idx)
                    self.step(st, js, cwd_default, ref, depth)

    def matrices(self, job: dict, scope: Scope) -> list[dict[str, str]]:
        m = (job.get("strategy") or {}).get("matrix")
        if not isinstance(m, dict):
            return [{}]
        keys = [k for k, val in m.items() if k not in ("include", "exclude") and isinstance(val, list)]
        combos = [dict(zip(keys, vals, strict=True)) for vals in itertools.product(*(m[k] for k in keys))] if keys else []
        for inc in m.get("include") or []:
            if isinstance(inc, dict):
                combos.append({str(k): v for k, v in inc.items()})
        combos = combos or [{}]
        return [{k: (v if isinstance(v, str) else str(v)) for k, v in c.items()} for c in combos[:64]]

    def step_name(self, st: dict, scope: Scope) -> str:
        if "name" in st:
            return scope.sub(str(st["name"]))
        if "run" in st:
            first = next((ln for ln in str(st["run"]).splitlines() if ln.strip()), "")
            return "Run " + scope.sub(first.strip())
        return "Run " + scope.sub(str(st.get("uses", "")))

    def step(self, st: dict, scope: Scope, cwd_default: str, ref: StepRef, depth: int) -> None:
        ss = scope.child(env=st.get("env") or {})
        before = len(self.ctx.hits)
        if "run" in st:
            cwd = ss.sub(str(st.get("working-directory", cwd_default)))
            cwd = "." if WILD in cwd else norm(".", cwd)
            self.interp.run(ss.sub(str(st["run"])), cwd)
        elif "uses" in st:
            self.uses_step(str(st["uses"]), st, ss, cwd_default, depth)
        for h in self.ctx.hits[before:]:
            self.evidence.append(Evidence(h, ref))

    def uses_step(self, uses: str, st: dict, scope: Scope, cwd: str, depth: int) -> None:
        with_ = {k: scope.sub(str(val)) for k, val in (st.get("with") or {}).items()}
        name = uses.split("@", 1)[0]
        if name == "docker/build-push-action":
            ctxdir = norm(".", with_.get("context", "."))
            f = with_.get("file") or (f"{ctxdir}/Dockerfile" if ctxdir != "." else "Dockerfile")
            self.interp.emit_docker(norm(".", f), f"uses {name}")
            return
        if name in ("rhysd/actionlint", "raven-actions/actionlint", "reviewdog/action-actionlint", "devops-actions/actionlint"):
            self.interp.tool(["actionlint"], ".", f"uses {name}")
            return
        if name == "hadolint/hadolint-action":
            self.interp.emit_docker(with_.get("dockerfile", "Dockerfile"), f"uses {name}")
            return
        if name in ("cypress-io/github-action",):
            wd = with_.get("working-directory", cwd)
            u = self.ctx.unit_at(norm(".", wd), ("node",))
            if u:
                self.ctx.emit(u, {"e2e"}, f"uses {name}")
            return
        target = self.resolve_uses(uses, "action")
        if target is None or depth > 4:
            return
        doc = load(target)
        runs = doc.get("runs") or {}
        if runs.get("using") != "composite":
            return
        inputs = {}
        for k, spec in (doc.get("inputs") or {}).items():
            if isinstance(spec, dict) and "default" in spec:
                inputs[k] = str(spec["default"])
        inputs.update(with_)
        inner = Scope(inputs, scope.matrix, dict(scope.env))
        for s2 in runs.get("steps") or []:
            if not isinstance(s2, dict):
                continue
            s_scope = inner.child(env=s2.get("env") or {})
            if "run" in s2:
                wd = s_scope.sub(str(s2.get("working-directory", cwd)))
                wd = "." if WILD in wd else norm(".", wd)
                self.interp.run(s_scope.sub(str(s2["run"])), wd, f"[{uses.split('@')[0]}]")
            elif "uses" in s2:
                self.uses_step(str(s2["uses"]), s2, s_scope, cwd, depth + 1)
