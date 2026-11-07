import json
import tempfile
import unittest
from pathlib import Path

import dependency_diff as dd


def pkg(name: str, version: str = "1.0.0", groups=None, violations=None) -> dict:
    return {
        "package": {"name": name, "version": version, "ecosystem": "npm"},
        "groups": groups or [],
        "license_violations": violations or [],
    }


def report(*packages: dict) -> dict:
    return {"results": [{"source": {"path": "pnpm-lock.yaml"}, "packages": list(packages)}]}


HIGH = {"ids": ["GHSA-high"], "max_severity": "7.4"}
LOW = {"ids": ["GHSA-low"], "max_severity": "3.7"}
UNSCORED = {"ids": ["GHSA-none"], "max_severity": ""}


class FindingsTest(unittest.TestCase):
    def test_keeps_only_vulnerabilities_at_or_above_the_floor(self) -> None:
        got = dd.findings(report(pkg("a", groups=[HIGH, LOW])), dd.SEVERITY_FLOOR["high"])
        self.assertEqual([f.detail for f in got], ["GHSA-high (CVSS 7.4)"])

    def test_unscored_vulnerability_counts(self) -> None:
        got = dd.findings(report(pkg("a", groups=[UNSCORED])), 7.0)
        self.assertEqual([(f.detail, f.blocking) for f in got], [("GHSA-none (unscored)", True)])

    def test_unknown_license_is_reported_but_not_blocking(self) -> None:
        got = dd.findings(report(pkg("a", violations=["GPL-3.0", "UNKNOWN"])), 7.0)
        self.assertEqual([(f.detail, f.blocking) for f in got], [("GPL-3.0", True), ("UNKNOWN", False)])

    def test_empty_report(self) -> None:
        self.assertEqual(dd.findings({}, 7.0), [])


class AddedTest(unittest.TestCase):
    def test_existing_finding_is_not_new_even_after_a_version_bump(self) -> None:
        base = dd.findings(report(pkg("a", "1.0.0", groups=[HIGH])), 7.0)
        head = dd.findings(report(pkg("a", "1.0.1", groups=[HIGH])), 7.0)
        self.assertEqual(dd.added(base, head), [])

    def test_new_package_and_new_advisory_are_new(self) -> None:
        base = dd.findings(report(pkg("a", groups=[HIGH])), 7.0)
        head = dd.findings(
            report(pkg("a", groups=[HIGH, {"ids": ["GHSA-2"], "max_severity": "9.8"}]), pkg("b", violations=["AGPL-3.0"])),
            7.0,
        )
        self.assertEqual([(f.package, f.detail) for f in dd.added(base, head)], [("b", "AGPL-3.0"), ("a", "GHSA-2 (CVSS 9.8)")])

    def test_same_finding_in_two_lockfiles_is_listed_once(self) -> None:
        head = dd.findings({"results": [report(pkg("a", groups=[HIGH]))["results"][0]] * 2}, 7.0)
        self.assertEqual(len(dd.added([], head)), 1)


class MainTest(unittest.TestCase):
    def run_main(self, base: dict | None, head: dict) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            b, h, s = Path(tmp, "b.json"), Path(tmp, "h.json"), Path(tmp, "summary.md")
            b.write_text(json.dumps(base) if base is not None else "")
            h.write_text(json.dumps(head))
            code = dd.main(["--base", str(b), "--head", str(h), "--summary-file", str(s)])
            return code, s.read_text()

    def test_fails_on_an_added_vulnerability(self) -> None:
        code, summary = self.run_main(report(), report(pkg("a", groups=[HIGH])))
        self.assertEqual(code, 1)
        self.assertIn("| ❌ | vulnerability | npm a@1.0.0 | GHSA-high (CVSS 7.4) |", summary)

    def test_passes_when_only_warnings_are_added(self) -> None:
        code, summary = self.run_main(None, report(pkg("a", violations=["UNKNOWN"])))
        self.assertEqual(code, 0)
        self.assertIn("⚠️", summary)

    def test_passes_a_clean_pr(self) -> None:
        code, summary = self.run_main(report(pkg("a", groups=[HIGH])), report(pkg("a", groups=[HIGH])))
        self.assertEqual(code, 0)
        self.assertIn("No new vulnerable", summary)


if __name__ == "__main__":
    unittest.main()
