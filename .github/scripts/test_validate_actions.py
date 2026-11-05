import tempfile
import unittest
from pathlib import Path

import validate_actions as va


def composite(**overrides: object) -> dict:
    meta = {
        "name": "x",
        "description": "d",
        "inputs": {"a": {"description": "in", "default": ""}},
        "outputs": {"o": {"description": "out", "value": "${{ steps.s.outputs.o }}"}},
        "runs": {"using": "composite", "steps": [{"id": "s", "run": "true", "shell": "bash"}]},
    }
    meta.update(overrides)
    return meta


class CheckAction(unittest.TestCase):
    def test_valid_composite(self) -> None:
        self.assertEqual(va.check_action(composite()), [])

    def test_valid_node(self) -> None:
        self.assertEqual(va.check_action({"name": "n", "description": "d", "runs": {"using": "node24", "main": "i.js"}}), [])

    def test_not_a_mapping(self) -> None:
        self.assertEqual(va.check_action(["x"]), ["not a mapping"])

    def test_missing_required_and_unknown_top_key(self) -> None:
        errors = va.check_action({"nmae": "x", "runs": {"using": "node24"}})
        self.assertIn("unknown top-level key 'nmae'", errors)
        self.assertIn("missing 'name'", errors)
        self.assertIn("missing 'description'", errors)

    def test_bad_runner(self) -> None:
        errors = va.check_action(composite(runs={"using": "node12"}))
        self.assertEqual(len(errors), 1)
        self.assertIn("runs.using 'node12'", errors[0])

    def test_input_without_description_and_unknown_key(self) -> None:
        errors = va.check_action(composite(inputs={"a": {"default": ""}, "b": {"description": "x", "requried": True}}))
        self.assertIn("input 'a' has no description", errors)
        self.assertIn("input 'b': unknown key 'requried'", errors)

    def test_composite_output_without_value(self) -> None:
        self.assertIn("output 'o' has no value", va.check_action(composite(outputs={"o": {"description": "x"}})))

    def test_run_without_shell(self) -> None:
        errors = va.check_action(composite(runs={"using": "composite", "steps": [{"name": "s", "run": "true"}]}))
        self.assertEqual(errors, ["step 1 (s): 'run' step has no 'shell'"])

    def test_step_needs_exactly_one_of_run_or_uses(self) -> None:
        both = composite(runs={"using": "composite", "steps": [{"run": "x", "shell": "bash", "uses": "a@b"}]})
        neither = composite(runs={"using": "composite", "steps": [{"name": "s"}]})
        self.assertIn("step 1: needs exactly one of 'run' or 'uses'", va.check_action(both))
        self.assertIn("step 1 (s): needs exactly one of 'run' or 'uses'", va.check_action(neither))

    def test_step_unknown_key_and_no_steps(self) -> None:
        self.assertIn("step 1: unknown key 'wiht'",
                      va.check_action(composite(runs={"using": "composite", "steps": [{"uses": "a@b", "wiht": {}}]})))
        self.assertIn("composite action has no steps", va.check_action(composite(runs={"using": "composite"})))


class Main(unittest.TestCase):
    def run_on(self, files: dict[str, str]) -> int:
        with tempfile.TemporaryDirectory() as tmp:
            for rel, text in files.items():
                path = Path(tmp, rel)
                path.parent.mkdir(parents=True)
                path.write_text(text)
            return va.main(["--root", tmp])

    def test_passes_on_valid(self) -> None:
        self.assertEqual(self.run_on({"a/action.yml": "name: a\ndescription: d\nruns: {using: node24, main: i.js}\n"}), 0)

    def test_fails_on_invalid(self) -> None:
        self.assertEqual(self.run_on({"a/action.yaml": "name: a\nruns: {using: node24}\n"}), 1)

    def test_fails_when_empty(self) -> None:
        self.assertEqual(self.run_on({}), 1)

    def test_repo_actions_are_valid(self) -> None:
        root = Path(__file__).resolve().parent.parent / "actions"
        self.assertEqual(va.main(["--root", str(root)]), 0)


if __name__ == "__main__":
    unittest.main()
