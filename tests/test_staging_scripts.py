from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = sorted((ROOT / "scripts" / "staging").glob("*.sh"))


class StagingScriptTests(unittest.TestCase):
    def test_safety_preamble(self) -> None:
        self.assertEqual(len(SCRIPTS), 5)
        for script in SCRIPTS:
            text = script.read_text()
            self.assertTrue(
                text.startswith("#!/usr/bin/env bash\nset -euo pipefail\n"),
                script.name,
            )
            self.assertNotIn("set -x", text)

    def test_forbidden_commands_absent(self) -> None:
        combined = "\n".join(script.read_text() for script in SCRIPTS)
        forbidden = (
            "eval ",
            "source .env.staging",
            "down -v",
            "volume rm",
            "system prune",
            "alembic downgrade",
            "--parallel",
            ":latest",
        )
        for value in forbidden:
            self.assertNotIn(value, combined)

    def test_compose_command_is_explicit_and_centralized(self) -> None:
        lib = (ROOT / "scripts/staging/lib.sh").read_text()
        self.assertIn('--project-directory "${STAGING_ROOT}"', lib)
        self.assertIn('--env-file "${STAGING_ENV_FILE}"', lib)
        self.assertIn('-p "${STAGING_PROJECT}"', lib)
        self.assertIn('-f "${STAGING_COMPOSE_FILE}"', lib)
        for script in SCRIPTS:
            if script.name != "lib.sh":
                self.assertNotRegex(
                    script.read_text(), r"docker\s+(ps|inspect).*(name=|grep)"
                )

    def test_no_fuzzy_destructive_matching(self) -> None:
        stop = (ROOT / "scripts/staging/stop.sh").read_text()
        self.assertIn("com.docker.compose.project=${STAGING_PROJECT}", stop)
        self.assertRegex(stop, re.escape('"${COMPOSE[@]}" down'))
        self.assertNotIn("--remove-orphans", stop)

    def test_phase_state_consistency(self) -> None:
        for name in (
            "ROADMAP.md",
            "PROJECT_STATUS.md",
            "PHASE_1D_CONTROL_PLANE.md",
            "CHANGELOG.md",
            "STAGING.md",
        ):
            text = (ROOT / "docs" / name).read_text()
            self.assertIn("implemented_pending_verification", text, name)
            self.assertIn("in_progress", text, name)
            self.assertIn("not_started", text, name)

    def test_markdown_internal_links_exist(self) -> None:
        import re

        for document in (ROOT / "docs").rglob("*.md"):
            for target in re.findall(
                r"\[[^]]+\]\(([^)]+)\)", document.read_text()
            ):
                if target.startswith(("http://", "https://", "#")):
                    continue
                path = target.split("#", 1)[0]
                if path:
                    self.assertTrue(
                        (document.parent / path).resolve().exists(),
                        f"{document}: {target}",
                    )


if __name__ == "__main__":
    unittest.main()
