import json
import random
import unittest
from datetime import datetime, timedelta, timezone

import auto_update_pr as aup

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


def iso(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def pr(n: int, *, draft: bool = False, auto: bool = True, labels: tuple[str, ...] = (),
       checks: list[dict] | None = None) -> dict:
    return {"number": n, "isDraft": draft, "autoMergeRequest": {} if auto else None,
            "labels": [{"name": x} for x in labels], "headRefOid": f"sha{n}",
            "statusCheckRollup": checks or []}


RUNNING = {"status": "IN_PROGRESS", "startedAt": iso(5)}
HUNG = {"status": "IN_PROGRESS", "startedAt": iso(500)}
FAILED = {"status": "COMPLETED", "conclusion": "FAILURE"}
PASSED = {"status": "COMPLETED", "conclusion": "SUCCESS"}


class FakeGh:
    def __init__(self, prs: list[dict], states: dict[int, tuple[str, str]] | None = None,
                 threads: dict[int, int] | None = None, runs: list[dict] | None = None):
        self.prs, self.states, self.threads, self.runs = prs, states or {}, threads or {}, runs or []
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if args[:2] == ["pr", "list"]:
            return json.dumps(self.prs)
        if args[:2] == ["pr", "view"]:
            m, s = self.states.get(int(args[2]), ("MERGEABLE", "BEHIND"))
            return json.dumps({"mergeable": m, "mergeStateStatus": s})
        if args[:2] == ["api", "graphql"]:
            n = int(next(a for a in args if a.startswith("number=")).split("=")[1])
            return str(self.threads.get(n, 0))
        if args[0] == "api" and "actions/runs?" in args[1]:
            return json.dumps({"workflow_runs": self.runs})
        return ""

    def updated(self) -> list[str]:
        return [c[3].split("/")[4] for c in self.calls if c[:3] == ["api", "-X", "PUT"]]

    def label_edits(self) -> list[tuple[str, str]]:
        return [(c[2], c[5]) for c in self.calls if c[:2] == ["pr", "edit"]]

    def cancelled(self) -> list[str]:
        return [c[3] for c in self.calls if c[:3] == ["api", "-X", "POST"]]


def updater(fake: FakeGh, seed: int = 0) -> aup.Updater:
    return aup.Updater("o/r", "main", "auto-update", run=fake, sleep=lambda _: None, rng=random.Random(seed))


class StepTest(unittest.TestCase):
    def test_updates_one_eligible_pr_and_labels_it(self) -> None:
        fake = FakeGh([pr(1), pr(2, draft=True), pr(3, auto=False)])
        self.assertEqual(updater(fake).step("schedule", NOW), 1)
        self.assertEqual(fake.updated(), ["1"])
        self.assertIn(("1", "--add-label"), fake.label_edits())

    def test_stands_down_when_any_non_draft_pr_has_ci_running(self) -> None:
        fake = FakeGh([pr(1), pr(2, auto=False, checks=[RUNNING])])
        self.assertIsNone(updater(fake).step("schedule", NOW))
        self.assertEqual(fake.updated(), [])

    def test_draft_ci_does_not_count_as_busy(self) -> None:
        fake = FakeGh([pr(1), pr(2, draft=True, checks=[RUNNING])])
        self.assertEqual(updater(fake).step("schedule", NOW), 1)

    def test_hung_check_is_not_busy(self) -> None:
        fake = FakeGh([pr(1), pr(2, checks=[HUNG])], states={2: ("MERGEABLE", "BLOCKED")})
        self.assertEqual(updater(fake).step("schedule", NOW), 1)

    def test_skips_conflicting_not_behind_and_unresolved(self) -> None:
        fake = FakeGh([pr(1), pr(2), pr(3), pr(4)],
                      states={1: ("CONFLICTING", "DIRTY"), 2: ("MERGEABLE", "CLEAN"), 3: ("MERGEABLE", "BEHIND")},
                      threads={3: 2})
        self.assertEqual(updater(fake).step("schedule", NOW), 4)

    def test_unknown_merge_state_is_skipped_after_polling(self) -> None:
        fake = FakeGh([pr(1)], states={1: ("UNKNOWN", "UNKNOWN")})
        self.assertIsNone(updater(fake).step("schedule", NOW))

    def test_choice_is_random(self) -> None:
        picks = {updater(FakeGh([pr(1), pr(2), pr(3)]), seed=s).step("schedule", NOW) for s in range(20)}
        self.assertGreater(len(picks), 1)

    def test_failed_labelled_pr_is_released_and_another_chosen(self) -> None:
        fake = FakeGh([pr(1, labels=("auto-update",), checks=[FAILED]), pr(2)],
                      states={1: ("MERGEABLE", "BLOCKED")})
        self.assertEqual(updater(fake).step("workflow_run", NOW), 2)
        self.assertIn(("1", "--remove-label"), fake.label_edits())
        self.assertIn(("2", "--add-label"), fake.label_edits())

    def test_push_restarts_labelled_pr_with_running_ci(self) -> None:
        runs = [{"id": 7, "status": "in_progress"}, {"id": 8, "status": "completed"}]
        fake = FakeGh([pr(1, labels=("auto-update",), checks=[RUNNING]), pr(2)], runs=runs)
        self.assertEqual(updater(fake).step("push", NOW), 1)
        self.assertEqual(fake.cancelled(), ["repos/o/r/actions/runs/7/cancel"])
        self.assertEqual(fake.updated(), ["1"])

    def test_schedule_waits_for_labelled_pr_with_running_ci(self) -> None:
        fake = FakeGh([pr(1, labels=("auto-update",), checks=[RUNNING]), pr(2)])
        self.assertIsNone(updater(fake).step("schedule", NOW))
        self.assertEqual(fake.cancelled(), [])

    def test_passed_labelled_pr_behind_again_keeps_label(self) -> None:
        fake = FakeGh([pr(1, labels=("auto-update",), checks=[PASSED])])
        self.assertEqual(updater(fake).step("push", NOW), 1)
        self.assertEqual([e for e in fake.label_edits() if e[0] == "1"], [])

    def test_label_moves_off_previous_pr(self) -> None:
        fake = FakeGh([pr(1, labels=("auto-update",), checks=[PASSED]), pr(2)],
                      states={1: ("MERGEABLE", "CLEAN")})
        self.assertEqual(updater(fake).step("schedule", NOW), 2)
        self.assertIn(("1", "--remove-label"), fake.label_edits())

    def test_label_is_created(self) -> None:
        fake = FakeGh([])
        updater(fake).step("schedule", NOW)
        self.assertEqual(fake.calls[0][:3], ["label", "create", "auto-update"])


class HelpersTest(unittest.TestCase):
    def test_status_context_pending(self) -> None:
        p = aup.PR(1, False, True, set(), "s", [{"state": "PENDING", "createdAt": iso(1)}])
        self.assertTrue(aup.ci_running(p, NOW, 90))

    def test_status_context_error_is_failure(self) -> None:
        self.assertTrue(aup.ci_failed(aup.PR(1, False, True, set(), "s", [{"state": "ERROR"}])))

    def test_main_parses_args(self) -> None:
        orig = aup.Updater.step
        seen: list[str] = []
        aup.Updater.step = lambda self, event, now: seen.append(event)  # type: ignore[method-assign]
        try:
            self.assertEqual(aup.main(["--repo", "o/r", "--event", "push"]), 0)
        finally:
            aup.Updater.step = orig  # type: ignore[method-assign]
        self.assertEqual(seen, ["push"])


if __name__ == "__main__":
    unittest.main()
