import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import read_toolchain


def toml(text: str) -> Path:
    d = tempfile.mkdtemp()
    path = Path(d) / "rust-toolchain.toml"
    path.write_text(text)
    return path


class TestRead(unittest.TestCase):
    def test_full(self) -> None:
        path = toml(
            '[toolchain]\nchannel = "1.98.1"\n'
            'targets = ["wasm32-unknown-unknown", "x86_64-unknown-linux-musl"]\ncomponents = ["rustfmt", "clippy"]\n'
        )
        self.assertEqual(
            read_toolchain.read(path),
            {"toolchain": "1.98.1", "targets": "wasm32-unknown-unknown,x86_64-unknown-linux-musl", "components": "rustfmt,clippy"},
        )

    def test_no_targets_line(self) -> None:
        path = toml('[toolchain]\nchannel = "1.98.1"\ncomponents = ["rustfmt"]\n')
        self.assertEqual(read_toolchain.read(path), {"toolchain": "1.98.1", "targets": "", "components": "rustfmt"})

    def test_multiline_lists(self) -> None:
        path = toml('[toolchain]\nchannel = "stable"\ntargets = [\n  "a",\n  "b",\n]\n')
        self.assertEqual(read_toolchain.read(path)["targets"], "a,b")

    def test_missing_channel(self) -> None:
        with self.assertRaises(ValueError):
            read_toolchain.read(toml("[toolchain]\n"))

    def test_main_prints_output_lines(self) -> None:
        path = toml('[toolchain]\nchannel = "1.98.1"\n')
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(read_toolchain.main(["--file", str(path)]), 0)
        self.assertEqual(out.getvalue(), "toolchain=1.98.1\ntargets=\ncomponents=\n")


if __name__ == "__main__":
    unittest.main()
