"""Migration command regressions. Run: python3 -m unittest discover -s tests"""
import _isolation  # noqa: F401
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "ansible/roles/boxd-clickhouse/tasks/main.yml"


class ClickHouseMigrations(unittest.TestCase):
    def run_migrations(self, fail_first=False):
        task = TASKS.read_text().split("- name: Apply schema migrations\n", 1)[1]
        task = task.split("\n- name:", 1)[0]
        script = textwrap.dedent(
            task.split("  shell: |\n", 1)[1].split("  environment:", 1)[0]
        )
        self.assertIn('CLICKHOUSE_PASSWORD: "{{ clickhouse_password }}"', task)
        self.assertIn("no_log: true", task)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            migrations = root / "migrations"
            migrations.mkdir()
            (migrations / "001.sql").write_text("SELECT 1; SELECT 2;\n")
            (migrations / "002.sql").write_text("SELECT 3;\n")
            client = root / "clickhouse-client"
            client.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "sql = sys.stdin.read()\n"
                "with open(os.environ['CALL_LOG'], 'a') as log:\n"
                "    log.write(json.dumps({'args': sys.argv[1:], 'sql': sql,\n"
                "        'has_password': bool(os.environ.get('CLICKHOUSE_PASSWORD'))}) + '\\n')\n"
                "sys.exit(36 if os.environ['FAIL_FIRST'] == '1' and 'SELECT 1' in sql else 0)\n"
            )
            client.chmod(0o755)
            log = root / "calls.jsonl"
            script = script.replace("/tmp/clickhouse-migrations", str(migrations))
            script = script.replace("{{ vrack_ip | quote }}", "10.60.0.10")
            env = {
                **os.environ,
                "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "CLICKHOUSE_PASSWORD": "test-only",
                "CALL_LOG": str(log),
                "FAIL_FIRST": str(int(fail_first)),
            }
            result = subprocess.run(["/bin/sh", "-c", script], env=env, capture_output=True)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
        return result.returncode, calls

    def test_migrations_use_environment_password_and_no_redundant_flag(self):
        status, calls = self.run_migrations()
        self.assertEqual(status, 0)
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertEqual(call["args"], ["--host", "10.60.0.10"])
            self.assertTrue(call["has_password"])
        self.assertEqual(calls[0]["sql"], "SELECT 1; SELECT 2;\n")

    def test_first_failure_stops_remaining_migrations(self):
        status, calls = self.run_migrations(fail_first=True)
        self.assertEqual(status, 36)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
