import unittest

from detect_languages import detect, package_manager


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

    def test_go_and_php_detected(self) -> None:
        out = detect(["main.go", "go.mod", "plugin/x.php"], ["main.go"])
        self.assertTrue(out["go-changed"])
        self.assertTrue(out["has-php"])
        self.assertFalse(out["php-changed"])

    def test_npm_lockfile_changes_every_present_language(self) -> None:
        out = detect(["a.ts", "x.php"], ["package-lock.json"])
        self.assertTrue(out["js-changed"])
        self.assertTrue(out["php-changed"])


class PackageManagerTest(unittest.TestCase):
    def test_pnpm(self) -> None:
        self.assertEqual(package_manager(["package.json", "pnpm-lock.yaml"]), "pnpm")

    def test_npm(self) -> None:
        self.assertEqual(package_manager(["package.json", "package-lock.json"]), "npm")

    def test_pnpm_wins_over_npm(self) -> None:
        self.assertEqual(package_manager(["package-lock.json", "pnpm-lock.yaml"]), "pnpm")

    def test_nested_lockfile_is_not_the_repo_manager(self) -> None:
        self.assertEqual(package_manager(["docs/package-lock.json", "a.py"]), "")


if __name__ == "__main__":
    unittest.main()
