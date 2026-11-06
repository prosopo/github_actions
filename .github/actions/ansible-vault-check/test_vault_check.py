import contextlib
import io
import tempfile
import textwrap
import unittest
from pathlib import Path

import vault_check

VAULT = """!vault |
    $ANSIBLE_VAULT;1.1;AES256
    3132333435363738"""

CLASSIFICATION = """\
sensitive: [db_password, api_token]
plain: [ansible_host, port]
pending_encryption: [legacy_key]
"""


class VaultCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.inventory = self.root / "inventories"
        self.classification = self.root / "sensitive_keys.yml"
        self.classification.write_text(CLASSIFICATION)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, rel: str, body: str) -> None:
        path = self.inventory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body))

    def run_main(self, *extra: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = vault_check.main(
                [
                    "--inventory-dir",
                    str(self.inventory),
                    "--classification",
                    str(self.classification),
                    "--root",
                    str(self.root),
                    *extra,
                ]
            )
        return code, out.getvalue()

    def test_encrypted_sensitive_value_passes(self) -> None:
        self.write(
            "production/group_vars/all.yml",
            f"ansible_host: 10.0.0.1\nenv_file:\n  db_password: {VAULT}\n",
        )
        code, out = self.run_main()
        self.assertEqual(code, 0, out)

    def test_plaintext_sensitive_value_fails(self) -> None:
        self.write("production/group_vars/all.yml", "db_password: hunter2\n")
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("sensitive key 'db_password' is not encrypted", out)
        self.assertNotIn("hunter2", out)

    def test_nested_host_vars_directory_is_checked(self) -> None:
        self.write("staging/host_vars/web1/main.yml", "env:\n  api_token: abc\n")
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("staging/host_vars/web1/main.yml", out)
        self.assertIn("'env.api_token'", out)

    def test_unclassified_key_fails(self) -> None:
        self.write("production/group_vars/all.yml", "mystery: 1\n")
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("unclassified key 'mystery'", out)

    def test_whole_file_vault_fails(self) -> None:
        self.write("production/group_vars/all.yml", "$ANSIBLE_VAULT;1.1;AES256\n3132\n")
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("whole-file encrypted", out)

    def test_pending_key_warns_but_passes(self) -> None:
        self.write("production/group_vars/all.yml", "legacy_key: abc\n")
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("::warning::", out)
        self.assertIn("pending encryption", out)

    def test_reference_fails_without_flag(self) -> None:
        self.write("production/group_vars/all.yml", 'db_password: "{{ vault_db }}"\n')
        code, _ = self.run_main()
        self.assertEqual(code, 1)

    def test_reference_passes_with_flag(self) -> None:
        self.write(
            "production/group_vars/all.yml",
            'db_password: "{{ vault_db }}"\napi_token: "x://u:${PW}@h"\n',
        )
        code, out = self.run_main("--allow-references")
        self.assertEqual(code, 0, out)

    def test_env_filter_limits_files(self) -> None:
        self.write("production/group_vars/all.yml", "port: 1\n")
        self.write("development/group_vars/all.yml", "db_password: plain\n")
        code, out = self.run_main("--env", "production")
        self.assertEqual(code, 0, out)

    def test_empty_file_is_ignored(self) -> None:
        self.write("production/group_vars/all.yml", "port: 1\n")
        self.write("production/group_vars/empty.yml", "---\n")
        code, out = self.run_main()
        self.assertEqual(code, 0, out)

    def test_non_mapping_fails(self) -> None:
        self.write("production/group_vars/all.yml", "- a\n- b\n")
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("not a YAML mapping", out)

    def test_overlapping_classification_is_config_error(self) -> None:
        self.classification.write_text("sensitive: [a]\nplain: [a]\n")
        self.write("production/group_vars/all.yml", "a: 1\n")
        code, out = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("keys in both sensitive and plain", out)

    def test_no_vars_files_is_config_error(self) -> None:
        self.inventory.mkdir()
        code, out = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("no vars files", out)

    def test_missing_inventory_is_config_error(self) -> None:
        code, out = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("inventory directory not found", out)

    def test_flatten_nests_with_dots(self) -> None:
        self.assertEqual(
            vault_check.flatten({"a": {"b": 1}, "c": 2}), {"a.b": 1, "c": 2}
        )


if __name__ == "__main__":
    unittest.main()
