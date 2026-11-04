import unittest

from detect_languages import detect


class DetectTest(unittest.TestCase):
    def test_absent_language_is_never_changed(self) -> None:
        out = detect(["src/a.ts"], ["src/a.ts", "x.py"])
        self.assertFalse(out["has-python"])
        self.assertFalse(out["python-changed"])

    def test_untouched_language_is_skipped(self) -> None:
        out = detect(["a.ts", "crates/x/src/lib.rs"], ["a.ts"])
        self.assertTrue(out["has-rust"])
        self.assertFalse(out["rust-changed"])
        self.assertTrue(out["js-changed"])

    def test_workflow_edit_changes_every_present_language(self) -> None:
        out = detect(["a.ts", "b.rs"], [".github/workflows/ci.yml"])
        self.assertTrue(out["js-changed"])
        self.assertTrue(out["rust-changed"])
        self.assertFalse(out["python-changed"])

    def test_unknown_base_changes_everything_present(self) -> None:
        out = detect(["pyproject.toml", "docker/provider.Dockerfile"], None)
        self.assertTrue(out["python-changed"])
        self.assertTrue(out["docker-changed"])
        self.assertFalse(out["js-changed"])

    def test_nested_manifest_matches(self) -> None:
        out = detect(["packages/x/Cargo.toml"], ["packages/x/Cargo.toml"])
        self.assertTrue(out["rust-changed"])


if __name__ == "__main__":
    unittest.main()
