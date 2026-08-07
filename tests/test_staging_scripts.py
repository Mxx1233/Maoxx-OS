import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = sorted((ROOT / "scripts" / "staging").glob("*.sh"))


class StagingScriptTests(unittest.TestCase):
    def run_lib(self, command: str, *, env: dict[str, str] | None = None):
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        return subprocess.run(
            [
                "bash",
                "-c",
                f'source "{ROOT / "scripts/staging/lib.sh"}"; {command}',
            ],
            env=process_env,
            capture_output=True,
            text=True,
        )

    def make_critical_git_tree(self, root: Path) -> str:
        files = (
            "scripts/staging/deploy.sh",
            "scripts/staging/lib.sh",
            "scripts/staging/preflight.sh",
            "scripts/staging/verify.sh",
            "scripts/staging/stop.sh",
            "compose.staging.yml",
            "Dockerfile",
            "app/main.py",
            "alembic/versions/0001.py",
            "alembic.ini",
            "requirements.txt",
        )
        for name in files:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"fixture for {name}\n")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Staging Test",
                "-c",
                "user.email=staging-test@example.invalid",
                "commit",
                "-qm",
                "fixture",
            ],
            cwd=root,
            check=True,
        )
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

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
            "--volumes",
            "--remove-orphans",
            "volume rm",
            "system prune",
            "alembic downgrade",
            "--parallel",
            ":latest",
            "git reset",
            "git clean",
            "git checkout",
        )
        for value in forbidden:
            self.assertNotIn(value, combined)

    def test_immutable_trust_chain_precedes_build(self) -> None:
        deploy = (ROOT / "scripts/staging/deploy.sh").read_text()
        preflight = (ROOT / "scripts/staging/preflight.sh").read_text()
        lib = (ROOT / "scripts/staging/lib.sh").read_text()
        self.assertLess(
            preflight.index("git fetch --prune origin"),
            preflight.index('require_approved_checkout "$target_sha"'),
        )
        self.assertLess(
            deploy.index('require_approved_checkout "$target_sha"'),
            deploy.index('docker build --tag "$image"'),
        )
        for path in (
            "scripts/staging/deploy.sh",
            "scripts/staging/lib.sh",
            "scripts/staging/preflight.sh",
            "scripts/staging/verify.sh",
            "scripts/staging/stop.sh",
            "compose.staging.yml",
            "Dockerfile",
            "app",
            "alembic",
            "alembic.ini",
            "requirements.txt",
        ):
            self.assertIn(path, lib)

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

    def test_approved_checkout_requires_matching_clean_head(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = self.make_critical_git_tree(root)
            passed = self.run_lib(
                'require_approved_checkout "$TARGET_SHA" "$TEST_ROOT"',
                env={"TARGET_SHA": target, "TEST_ROOT": str(root)},
            )
            self.assertEqual(passed.returncode, 0, passed.stderr)

            mismatch = self.run_lib(
                'require_approved_checkout "$TARGET_SHA" "$TEST_ROOT"',
                env={"TARGET_SHA": "0" * 40, "TEST_ROOT": str(root)},
            )
            self.assertNotEqual(mismatch.returncode, 0)
            self.assertIn("HEAD does not equal", mismatch.stderr)

            (root / "Dockerfile").write_text("tracked change\n")
            dirty = self.run_lib(
                'require_approved_checkout "$TARGET_SHA" "$TEST_ROOT"',
                env={"TARGET_SHA": target, "TEST_ROOT": str(root)},
            )
            self.assertNotEqual(dirty.returncode, 0)
            self.assertIn("completely clean", dirty.stderr)
            subprocess.run(
                ["git", "restore", "Dockerfile"], cwd=root, check=True
            )

            (root / "untracked-build-input.txt").write_text("untracked\n")
            untracked = self.run_lib(
                'require_approved_checkout "$TARGET_SHA" "$TEST_ROOT"',
                env={"TARGET_SHA": target, "TEST_ROOT": str(root)},
            )
            self.assertNotEqual(untracked.returncode, 0)
            self.assertIn("completely clean", untracked.stderr)

    def test_partial_compose_up_failure_runs_safe_down(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            log = root / "docker.log"
            docker = fake_bin / "docker"
            docker.write_text(
                "#!/usr/bin/env bash\n"
                'printf \'%s\\n\' "$*" >> "$STAGING_TEST_LOG"\n'
                'case "$*" in\n'
                "  'ps -aq --filter label=com.docker.compose.project=maoxx-staging') echo partial-container ;;\n"
                "  *' up -d db') exit 17 ;;\n"
                "  *' down') exit 0 ;;\n"
                "esac\n"
            )
            docker.chmod(0o755)
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    f'source "{ROOT / "scripts/staging/deploy.sh"}"; '
                    "arm_deploy_cleanup; start_staging_db",
                ],
                env={
                    **os.environ,
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "STAGING_TEST_LOG": str(log),
                },
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 17)
            calls = log.read_text().splitlines()
            self.assertTrue(any(call.endswith(" up -d db") for call in calls))
            self.assertTrue(any(call.endswith(" down") for call in calls))
            combined = " ".join(calls)
            for forbidden in (
                " -v",
                "--volumes",
                "--remove-orphans",
                "volume rm",
                "prune",
            ):
                self.assertNotIn(forbidden, combined)

    def test_newer_non_successful_ci_run_rejects_old_success(self) -> None:
        sha = "a" * 40
        for status, conclusion in (
            ("queued", None),
            ("in_progress", None),
            ("completed", "failure"),
            ("completed", "cancelled"),
            ("completed", "timed_out"),
        ):
            with self.subTest(status=status, conclusion=conclusion):
                fixture = {
                    "workflow_runs": [
                        {
                            "id": 1,
                            "run_attempt": 1,
                            "head_sha": sha,
                            "name": "CI",
                            "status": "completed",
                            "conclusion": "success",
                        },
                        {
                            "id": 2,
                            "run_attempt": 1,
                            "head_sha": sha,
                            "name": "CI",
                            "status": status,
                            "conclusion": conclusion,
                        },
                    ]
                }
                result = self.run_lib(
                    'latest_successful_ci_run_id "$CI_JSON" "$TARGET_SHA"',
                    env={"CI_JSON": json.dumps(fixture), "TARGET_SHA": sha},
                )
                self.assertNotEqual(result.returncode, 0)

    def test_production_staging_resource_ids_must_be_disjoint(self) -> None:
        passed = self.run_lib(
            "require_disjoint_resource_ids networks $'prod-a\\nprod-b' $'stage-a\\nstage-b'"
        )
        self.assertEqual(passed.returncode, 0, passed.stderr)
        for kind in ("networks", "volumes"):
            with self.subTest(kind=kind):
                failed = self.run_lib(
                    f"require_disjoint_resource_ids {kind} $'shared\\nprod-only' $'stage-only\\nshared'"
                )
                self.assertNotEqual(failed.returncode, 0)
                self.assertIn("identities overlap", failed.stderr)

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
