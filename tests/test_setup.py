"""Windows startup checks with isolated fixtures; never launches StarCraft II."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")


def ps_string(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(sys.platform == "win32" and POWERSHELL, "Windows PowerShell required")
class WindowsSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)/"project with spaces"
        self.root.mkdir()
        self.game = Path(self.temporary.name)/"SC2 游戏 [test] #1"
        (self.game/"SC2Data").mkdir(parents=True)
        (self.game/"Versions/Base123").mkdir(parents=True)
        (self.game/"Versions/Base123/SC2_x64.exe").write_bytes(b"fixture only")
        self.environment = dict(os.environ)
        self.environment.pop("SC2PATH", None)
        self.environment["PYTHONUTF8"] = "1"

    def configure(self, text="", environment=None):
        code = (
            "$ErrorActionPreference='Stop';"
            "[Console]::InputEncoding=[Text.UTF8Encoding]::new($false);"
            "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
            f". {ps_string(ROOT/'scripts/setup/config.ps1')};"
            f"Initialize-Sc2Path -ProjectRoot {ps_string(self.root)};"
            "Write-Output ('CONFIG=' + $env:SC2PATH)"
        )
        encoded = base64.b64encode(code.encode("utf-16-le")).decode("ascii")
        return subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
            input=text, encoding="utf-8", errors="replace", capture_output=True,
            env=environment or self.environment, timeout=60)

    def test_first_run_prompts_validates_saves_and_reuses_path(self):
        result = self.configure('"\n' + str(self.root/"missing game") + '\n"' + str(self.game) + '"\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONFIG=" + str(self.game), result.stdout)
        content = (self.root/".env").read_bytes()
        self.assertEqual(content.decode("utf-8").strip(), 'SC2PATH="' + str(self.game) + '"')
        result = self.configure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root/".env").read_bytes(), content)

    def test_preserves_other_settings_and_never_evaluates_dotenv(self):
        sentinel = self.root/"must-not-exist"
        other = "OTHER=$(New-Item " + str(sentinel) + ")"
        (self.root/".env").write_text(
            "# preserved\n" + other + "\nSC2PATH=Z:\\old\nSC2PATH=Z:\\duplicate\n", encoding="utf-8")
        environment = dict(self.environment, SC2PATH=str(self.game))
        result = self.configure(environment=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = (self.root/".env").read_text(encoding="utf-8")
        self.assertIn(other, saved)
        self.assertTrue(saved.startswith("# preserved\n"))
        self.assertEqual(saved.count("SC2PATH="), 1)
        self.assertFalse(sentinel.exists())

    def test_invalid_saved_path_can_be_corrected_and_empty_input_cancels(self):
        (self.root/".env").write_text("SC2PATH=" + str(self.root/"missing game") + "\n", encoding="utf-8")
        result = self.configure(str(self.game) + "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.root/".env").write_text("KEEP=value\n", encoding="utf-8")
        result = self.configure("\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root/".env").read_text(encoding="utf-8"), "KEEP=value\n")

    def make_launcher(self):
        (self.root/"scripts/setup").mkdir(parents=True)
        shutil.copy2(ROOT/"scripts/start-windows.ps1", self.root/"scripts/start-windows.ps1")
        shutil.copy2(ROOT/"start.cmd", self.root/"start.cmd")
        shutil.copy2(ROOT/"scripts/setup/config.ps1", self.root/"scripts/setup/config.ps1")
        (self.root/"scripts/setup/windows.ps1").write_text("Write-Output 'SETUP-CALLED'\n", encoding="ascii")
        scripts = self.root/".deps/sc2-win/Scripts"
        scripts.mkdir(parents=True)
        shutil.copy2(sys.executable, scripts/"python.exe")
        shutil.copy2(Path(sys.prefix)/"pyvenv.cfg", scripts.parent/"pyvenv.cfg")
        (self.root/"sc2").mkdir()
        (self.root/"sc2/__init__.py").write_text("", encoding="ascii")
        stub = "import json, os, sys\nprint('LAUNCHED=' + json.dumps({'module':__spec__.name,'args':sys.argv[1:],'path':os.getenv('SC2PATH')}))\n"
        for module in ("run", "lan"):
            (self.root/f"sc2/{module}.py").write_text(stub, encoding="ascii")
        (self.root/".env").write_text('SC2PATH="' + str(self.game) + '"\n', encoding="utf-8")

    def test_launcher_runs_setup_and_forwards_modes_arguments_and_replay_paths(self):
        self.make_launcher()
        cases = [([], "sc2.run"), (["host", "--control", "ai", "--key", "room password", "--no-window"], "sc2.lan"),
                 (["duel", "--no-window"], "sc2.run"),
                 (["join", "--address", "127.0.0.1"], "sc2.lan"),
                 (["watch", "--replay", "replay file.SC2Replay", "--windowed"], "sc2.run")]
        for arguments, module in cases:
            with self.subTest(arguments=arguments):
                result = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(self.root/"scripts/start-windows.ps1"), *arguments], cwd=self.temporary.name,
                    input="", encoding="utf-8", errors="replace", capture_output=True, env=self.environment, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("SETUP-CALLED", result.stdout)
                payload = json.loads(next(s[9:] for s in result.stdout.splitlines() if s.startswith("LAUNCHED=")))
                self.assertEqual(payload["module"], module)
                self.assertEqual(payload["path"], str(self.game))
                self.assertIn("latest", payload["args"])
                if not arguments:
                    self.assertEqual(payload["args"], ["human", "--ai", "default", "--version", "latest", "--native-ui", "--start-paused"])
                elif arguments[0] == "watch":
                    self.assertIn("--windowed", payload["args"])
                    self.assertEqual(payload["args"][2], str(Path(self.temporary.name)/"replay file.SC2Replay"))
                else:
                    self.assertEqual(payload["args"], arguments + (["--ai", "default"] if "ai" in arguments else []) + ["--version", "latest"])

    def test_ai_menu_and_explicit_flags(self):
        self.make_launcher()
        cases = [([], "8\n", "a6", True), ([], "a6\n", "a6", True),
                 (["human", "--ai", "a6"], "", "a6", False),
                 ([], "7\n", "a5", True), ([], "a5\n", "a5", True),
                 (["human", "--ai", "a5"], "", "a5", False),
                 ([], "6\n", "a4", True), (["human", "--ai", "a4"], "", "a4", False),
                 ([], "5\n", "a3", True), (["human", "--ai", "a3"], "", "a3", False),
                 ([], "4\n", "a2", True), (["human", "--ai", "a2"], "", "a2", False),
                 ([], "2\n", "a", True), ([], "invalid\n3\n", "b", True),
                 (["human", "--ai", "a"], "", "a", False),
                 (["human", "--ai=B"], "", "B", False),
                 (["host", "--control=ai"], "2\n", "a", True),
                 (["join", "--address", "127.0.0.1", "--control", "ai", "--ai", "b"], "", "b", False),
                 (["host", "--control", "ai", "--yes"], "", None, False)]
        for arguments, answer, expected, menu in cases:
            with self.subTest(arguments=arguments, answer=answer):
                result = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(self.root/"scripts/start-windows.ps1"), *arguments], cwd=self.temporary.name,
                    input=answer, encoding="utf-8", errors="replace", capture_output=True,
                    env=self.environment, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual("Choose AI:" in result.stdout, menu)
                payload = json.loads(next(s[9:] for s in result.stdout.splitlines() if s.startswith("LAUNCHED=")))
                forwarded = payload["args"]
                selected = next((s.split("=", 1)[1] for s in forwarded if s.startswith("--ai=")), None)
                if "--ai" in forwarded:
                    selected = forwarded[forwarded.index("--ai") + 1]
                self.assertEqual(selected, expected)

    def test_double_click_wrapper_handles_spaces_and_help_without_path_prompt(self):
        self.make_launcher()
        (self.root/".env").write_text("", encoding="utf-8")
        command = subprocess.list2cmdline([str(self.root/"start.cmd"), "host", "--help"])
        result = subprocess.run(command, shell=True,
            cwd=self.temporary.name, input="", encoding="utf-8", errors="replace", capture_output=True,
            env=self.environment, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"module": "sc2.lan"', result.stdout)
        self.assertEqual((self.root/".env").read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
