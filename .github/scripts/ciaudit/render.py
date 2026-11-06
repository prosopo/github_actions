"""Render an audit report dict as a self-contained HTML page."""

from __future__ import annotations

import html
from collections import Counter, defaultdict

from units import CATEGORIES

LABEL = {"yes": "yes", "unverified": "yes?", "manual": "manual", "no": "no", "n/a": "–", "opt-out": "opt-out"}


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def cell_html(c: dict) -> str:
    st = c["state"]
    cls = {"yes": "y", "unverified": "u", "manual": "m", "no": "n", "n/a": "na", "opt-out": "na"}[st]
    if not c["evidence"]:
        tip = c.get("reason") or c.get("recommendation") or ""
        return f'<td class="{cls}" title="{esc(tip)}">{LABEL[st]}</td>'
    links = []
    for e in c["evidence"][:3]:
        label = f"{e['workflow']}:{e['job']}"
        href = e.get("job_url") or e.get("run_url") or e["workflow_url"]
        status = e.get("status", "static")
        src = " (non-main)" if e.get("source") == "non-default-branch" else ""
        links.append(
            f'<a href="{esc(href)}" title="{esc(e["step"] + " | " + e["via"])}">{esc(label)}</a><small> {esc(status)}{src}</small>'
        )
    more = f"<small> +{len(c['evidence']) - 3}</small>" if len(c["evidence"]) > 3 else ""
    return f'<td class="{cls}"><b>{LABEL[st]}</b><br>' + "<br>".join(links) + more + "</td>"


def render(report: dict) -> str:
    owner = report["owner"]
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for u in report["units"]:
        by_repo[u["repo"]].append(u)
    totals = Counter(c["state"] for u in report["units"] for c in u["cells"].values())
    parts = [
        HEAD,
        "<h1>CI coverage audit</h1>",
        f'<p class="meta">{len(report["units"])} units across {len(by_repo)} repos. '
        f"Cells: {totals['yes']} yes (green run proves the step ran), {totals['unverified']} yes? (static only), "
        f"{totals['manual']} manual-only, {totals['no']} no, {totals['n/a']} n/a. "
        f"Verification against run logs: {'on' if report['verified'] else 'off'}.</p>",
        LEGEND,
        "<nav>" + " ".join(f'<a href="#r-{esc(r)}">{esc(r)}</a>' for r in by_repo) + ' <a href="#gaps">gaps</a></nav>',
    ]
    for repo, us in by_repo.items():
        meta = report["repos"].get(repo, {})
        sha = meta.get("sha", "")
        parts.append(
            f'<h2 id="r-{esc(repo)}"><a href="https://github.com/{owner}/{esc(repo)}">{esc(repo)}</a> '
            f'<small>@ <a href="https://github.com/{owner}/{esc(repo)}/tree/{esc(sha)}">{esc(sha[:10])}</a></small></h2>'
        )
        parts.append(
            '<div class="scroll"><table><thead><tr><th>unit</th><th>lang</th>'
            + "".join(f"<th>{c}</th>" for c in CATEGORIES)
            + "</tr></thead><tbody>"
        )
        for u in us:
            label = u["name"] if u["name"] == u["path"] else f"{u['name']} <small>{esc(u['path'])}</small>"
            parts.append(
                f'<tr><td class="unit">{label if u["name"] != u["path"] else esc(u["path"])}</td><td>{esc(u["language"])}</td>'
                + "".join(cell_html(u["cells"][c]) for c in CATEGORIES)
                + "</tr>"
            )
        parts.append("</tbody></table></div>")
    parts.append(
        '<h2 id="gaps">Flagged gaps</h2><p class="meta">Every applicable cell that is not a verified yes. Decide per row: add, or opt out with a reason.</p>'
    )
    for repo, us in by_repo.items():
        rows = [(u, c, u["cells"][c]) for u in us for c in CATEGORIES if u["cells"][c]["state"] in ("no", "unverified", "manual")]
        if not rows:
            continue
        parts.append(
            f'<h3>{esc(repo)} <small>{len(rows)}</small></h3><div class="scroll"><table class="gaps"><thead><tr><th>unit</th><th>check</th><th>state</th><th>recommendation</th></tr></thead><tbody>'
        )
        for u, c, cell in rows:
            parts.append(
                f'<tr><td>{esc(u["path"])} <small>{esc(u["kind"])}</small></td><td>{c}</td><td class="{ {"no": "n", "unverified": "u", "manual": "m"}[cell["state"]] }">{LABEL[cell["state"]]}</td><td>{esc(cell["recommendation"])}</td></tr>'
            )
        parts.append("</tbody></table></div>")
    if report["notes"]:
        parts.append(
            "<h2>Analysis notes</h2><details><summary>{} notes (unresolved references)</summary><ul>".format(len(report["notes"]))
            + "".join(f"<li><code>{esc(n)}</code></li>" for n in report["notes"][:400])
            + "</ul></details>"
        )
    parts.append(METHOD + "</main>")
    return "\n".join(parts)


LEGEND = """<p class="legend"><span class="y">yes</span> a green default-branch run shows the step succeeded
<span class="u">yes?</span> CI references it but no green run proves the step ran (skipped, filtered, PR-only)
<span class="m">manual</span> only dispatch/schedule/release workflows run it
<span class="n">no</span> nothing in CI does it <span class="na">–</span> not applicable (hover for why) <span class="na">opt-out</span> recorded opt-out (hover for reason)</p>"""

METHOD = """<h2>Method</h2><ul>
<li>Units: every package.json with a name or scripts, Cargo crate, pyproject/setup.py project (other Python grouped by top-level directory), go.mod, composer.json, Dockerfile, ansible.cfg tree, Gradle/Xcode/SwiftPM project and workflow set, discovered in a default-branch snapshot. Submodules (captcha-private/captcha, /protect) are mounted from their own snapshots so their packages appear under their own repo.</li>
<li>Coverage: every workflow with a trigger is interpreted statically: reusable workflows and composite actions (local and prosopo/github_actions) are followed with their inputs, matrix values expanded, and shell commands resolved through package scripts, pnpm/turbo --filter (incl. <code>pkg...</code> and <code>!./path/**</code>), turbo <code>dependsOn</code>, run-s globs, concurrently, Makefile targets and repo shell scripts. Root linters (<code>biome check .</code>, ruff, ansible-lint, actionlint) cover every unit under their path minus biome <code>files.includes</code> negations.</li>
<li>Verification: each referencing step is looked up in the latest successful run of its workflow on the default branch (or, for PR-only workflows, the latest successful run anywhere, marked non-main) and only a step concluding <code>success</code> counts as yes.</li>
<li>Limits: turbo cache hits look identical to real runs; shell <code>if</code>/<code>case</code> branches are flattened; <code>[git-ref]</code> filters are treated as matching all; npm script names alone imply a category (e.g. <code>test</code>) even when the body is a no-op.</li></ul>"""

HEAD = """<title>Prosopo CI Coverage</title><style>
:root{--bg:#fbfbfa;--fg:#1d1d1b;--mut:#6b6b66;--line:#e2e1dc;--y:#d8efdc;--u:#fff1c2;--m:#e6e0f5;--n:#f8d6d3;--na:#f0efeb;--a:#1f5fbf}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#161615;--fg:#e9e8e4;--mut:#a09f99;--line:#33332f;--y:#1e3b26;--u:#3e3517;--m:#2f2840;--n:#45201d;--na:#22221f;--a:#7fb0ff}}
:root[data-theme="dark"]{--bg:#161615;--fg:#e9e8e4;--mut:#a09f99;--line:#33332f;--y:#1e3b26;--u:#3e3517;--m:#2f2840;--n:#45201d;--na:#22221f;--a:#7fb0ff}
body{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:0}main{max-width:1400px;margin:0 auto;padding-block:16px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
a{color:var(--a)}h2{margin-top:2em;border-bottom:1px solid var(--line)}small,.meta{color:var(--mut)}nav a{margin-right:.6em;white-space:nowrap}
.scroll{overflow-x:auto}table{border-collapse:collapse;font-size:12.5px;min-width:100%}th,td{border:1px solid var(--line);padding:3px 6px;vertical-align:top;text-align:left}
th{position:sticky;top:0;background:var(--bg)}td.unit{max-width:320px;word-break:break-word}
.y{background:var(--y)}.u{background:var(--u)}.m{background:var(--m)}.n{background:var(--n)}.na{background:var(--na);color:var(--mut)}
.legend span{padding:1px 6px;border-radius:3px;margin-right:.3em}code{font-size:12px;word-break:break-all}
</style><main>"""
