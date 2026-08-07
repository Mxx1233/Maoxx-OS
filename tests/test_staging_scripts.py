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
            ".gitignore",
        )
        for name in files:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if name == ".gitignore":
                path.write_text(
                    "*.sql\n*.dump\n*.staging.local\n.env.staging\nstorage/\n"
                )
            else:
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
            deploy.index('approved_context="$(create_approved_build_context'),
        )
        self.assertLess(
            deploy.index('approved_context="$(create_approved_build_context'),
            deploy.index('build_approved_image "$approved_context" "$image"'),
        )
        self.assertNotIn('docker build --tag "$image" "$STAGING_ROOT"', deploy)
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

    def test_compose_labels_use_exact_dotted_map_key_lookup(self) -> None:
        combined = "\n".join(script.read_text() for script in SCRIPTS)
        self.assertNotIn(".Config.Labels.com.docker.compose", combined)
        self.assertIn(
            '{{index .Config.Labels "com.docker.compose.service"}}',
            (ROOT / "scripts/staging/stop.sh").read_text(),
        )

        for key, expected in (
            ("com.docker.compose.project", "maoxx-staging"),
            ("com.docker.compose.service", "api"),
        ):
            with (
                self.subTest(key=key),
                tempfile.TemporaryDirectory() as directory,
            ):
                fake_bin = Path(directory) / "bin"
                fake_bin.mkdir()
                log = Path(directory) / "docker.log"
                docker = fake_bin / "docker"
                docker.write_text(
                    "#!/usr/bin/env bash\n"
                    'printf \'%s\\n\' "$*" > "$STAGING_TEST_LOG"\n'
                    "printf '%s\\n' \"${STAGING_FAKE_LABEL_VALUE:-}\"\n"
                )
                docker.chmod(0o755)
                command = (
                    'require_container_compose_label container-id "$LABEL_KEY" '
                    '"$EXPECTED_LABEL" test'
                )
                base_env = {
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "STAGING_TEST_LOG": str(log),
                    "LABEL_KEY": key,
                    "EXPECTED_LABEL": expected,
                }

                passed = self.run_lib(
                    command,
                    env={**base_env, "STAGING_FAKE_LABEL_VALUE": expected},
                )
                self.assertEqual(passed.returncode, 0, passed.stderr)
                self.assertIn(
                    f'--format {{{{index .Config.Labels "{key}"}}}}',
                    log.read_text(),
                )

                wrong = self.run_lib(
                    command,
                    env={**base_env, "STAGING_FAKE_LABEL_VALUE": "wrong"},
                )
                self.assertNotEqual(wrong.returncode, 0)
                self.assertIn("wrong test label", wrong.stderr)

                missing = self.run_lib(command, env=base_env)
                self.assertNotEqual(missing.returncode, 0)
                self.assertIn("wrong test label", missing.stderr)

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

    def test_valid_predeploy_staging_states_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake_bin = Path(directory) / "bin"
            fake_bin.mkdir()
            docker = fake_bin / "docker"
            docker.write_text(
                "#!/usr/bin/env python3\n"
                "import os\n"
                "import sys\n"
                "args = sys.argv[1:]\n"
                "joined = ' '.join(args)\n"
                "failure = os.environ.get('STAGING_QUERY_FAILURE', '')\n"
                "def emit(name):\n"
                "    value = os.environ.get(name, '')\n"
                "    if value:\n"
                "        print(value)\n"
                "if args[:2] == ['ps', '-aq']:\n"
                "    sys.exit(23) if failure == 'containers' else emit('STAGING_CONTAINERS')\n"
                "elif args[:2] == ['network', 'ls']:\n"
                "    sys.exit(23) if failure == 'networks' else emit('STAGING_NETWORKS')\n"
                "elif args[:2] == ['volume', 'ls'] and 'label=com.docker.compose.project=maoxx-staging' in joined:\n"
                "    sys.exit(23) if failure == 'project_volumes' else emit('STAGING_PROJECT_VOLUMES')\n"
                "elif args[:2] == ['volume', 'ls'] and 'name=^maoxx-staging_' in joined:\n"
                "    sys.exit(23) if failure == 'named_volumes' else emit('STAGING_NAMED_VOLUMES')\n"
                "elif args[:2] == ['volume', 'inspect']:\n"
                "    if failure == 'inspect':\n"
                "        sys.exit(23)\n"
                "    if 'com.docker.compose.project' in joined:\n"
                "        emit('STAGING_PROJECT_LABEL')\n"
                "    elif 'com.docker.compose.volume' in joined:\n"
                "        emit('STAGING_VOLUME_LABEL')\n"
                "    else:\n"
                "        sys.exit(24)\n"
                "else:\n"
                "    sys.exit(24)\n"
            )
            docker.chmod(0o755)

            def check(**overrides: str):
                environment = {
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "STAGING_CONTAINERS": "",
                    "STAGING_NETWORKS": "",
                    "STAGING_PROJECT_VOLUMES": "",
                    "STAGING_NAMED_VOLUMES": "",
                    "STAGING_PROJECT_LABEL": "maoxx-staging",
                    "STAGING_VOLUME_LABEL": "postgres_data",
                    "STAGING_QUERY_FAILURE": "",
                    **overrides,
                }
                return self.run_lib(
                    "require_valid_predeploy_staging_state", env=environment
                )

            clean = check()
            self.assertEqual(clean.returncode, 0, clean.stderr)
            valid_retained = check(
                STAGING_PROJECT_VOLUMES="maoxx-staging_postgres_data",
                STAGING_NAMED_VOLUMES="maoxx-staging_postgres_data",
            )
            self.assertEqual(
                valid_retained.returncode, 0, valid_retained.stderr
            )

            rejected_states = (
                ("container", {"STAGING_CONTAINERS": "container-id"}),
                ("network", {"STAGING_NETWORKS": "network-id"}),
                (
                    "two volumes",
                    {
                        "STAGING_PROJECT_VOLUMES": "maoxx-staging_postgres_data\nother-volume",
                        "STAGING_NAMED_VOLUMES": "maoxx-staging_postgres_data",
                    },
                ),
                (
                    "wrong volume identity",
                    {
                        "STAGING_PROJECT_VOLUMES": "maoxx-staging_unknown",
                        "STAGING_NAMED_VOLUMES": "maoxx-staging_unknown",
                    },
                ),
                (
                    "wrong project label",
                    {
                        "STAGING_PROJECT_VOLUMES": "maoxx-staging_postgres_data",
                        "STAGING_NAMED_VOLUMES": "maoxx-staging_postgres_data",
                        "STAGING_PROJECT_LABEL": "wrong-project",
                    },
                ),
                (
                    "wrong volume label",
                    {
                        "STAGING_PROJECT_VOLUMES": "maoxx-staging_postgres_data",
                        "STAGING_NAMED_VOLUMES": "maoxx-staging_postgres_data",
                        "STAGING_VOLUME_LABEL": "wrong-volume",
                    },
                ),
                (
                    "Production volume",
                    {"STAGING_PROJECT_VOLUMES": "maoxx_postgres_data"},
                ),
                (
                    "unlabeled expected volume",
                    {"STAGING_NAMED_VOLUMES": "maoxx-staging_postgres_data"},
                ),
            )
            for name, environment in rejected_states:
                with self.subTest(state=name):
                    result = check(**environment)
                    self.assertNotEqual(result.returncode, 0)

            for query in (
                "containers",
                "networks",
                "project_volumes",
                "named_volumes",
                "inspect",
            ):
                with self.subTest(query_failure=query):
                    environment = {"STAGING_QUERY_FAILURE": query}
                    if query == "inspect":
                        environment.update(
                            STAGING_PROJECT_VOLUMES="maoxx-staging_postgres_data",
                            STAGING_NAMED_VOLUMES="maoxx-staging_postgres_data",
                        )
                    result = check(**environment)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("failed to", result.stderr)

    def test_docker_resource_line_sets_are_normalized_and_strict(self) -> None:
        verify = (ROOT / "scripts/staging/verify.sh").read_text()
        self.assertNotIn("mapfile", verify)
        self.assertEqual(verify.count("| normalize_line_set"), 2)
        self.assertNotIn("done | normalize_line_set", verify)
        self.assertIn("collect_container_network_ids", verify)
        self.assertIn("collect_container_volume_names", verify)

        def exact(input_lines: str, expected: str):
            return self.run_lib(
                'actual="$(printf \'%s\' "$LINE_SET_INPUT" | normalize_line_set)"; '
                "printf '%s' \"$actual\"; "
                'require_exact_line_set "$actual" "$EXPECTED_LINE_SET" test',
                env={
                    "LINE_SET_INPUT": input_lines,
                    "EXPECTED_LINE_SET": expected,
                },
            )

        internal = "maoxx-staging_staging_internal"
        api = "maoxx-staging_staging_api"
        expected_api = f"{api}\n{internal}"
        passing_sets = (
            ("blank DB records", f"\n{internal}\n\n", internal),
            (
                "blank API records",
                f"\n{internal}\n{api}\n\n",
                expected_api,
            ),
            ("different ordering", f"{internal}\n{api}\n", expected_api),
            (
                "duplicate expected network",
                f"{internal}\n{api}\n{internal}\n",
                expected_api,
            ),
        )
        for name, input_lines, expected in passing_sets:
            with self.subTest(valid_set=name):
                result = exact(input_lines, expected)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)

        rejected_sets = (
            (
                "unexpected third network",
                f"{internal}\n{api}\nother",
                expected_api,
            ),
            ("missing internal network", f"{api}\n", expected_api),
            ("wrong network name", "wrong-network\n", internal),
            ("empty-only required set", "\n\n", internal),
        )
        for name, input_lines, expected in rejected_sets:
            with self.subTest(invalid_set=name):
                result = exact(input_lines, expected)
                self.assertNotEqual(result.returncode, 0)

        for kind in ("networks", "volumes"):
            with self.subTest(blank_resource_ids=kind):
                blank_only = self.run_lib(
                    'production="$(printf \'%s\' "$PRODUCTION_IDS" | normalize_line_set)"; '
                    'staging="$(printf \'%s\' "$STAGING_IDS" | normalize_line_set)"; '
                    'require_disjoint_resource_ids "$RESOURCE_KIND" "$production" "$staging"',
                    env={
                        "RESOURCE_KIND": kind,
                        "PRODUCTION_IDS": "\n\n",
                        "STAGING_IDS": "\n",
                    },
                )
                self.assertEqual(blank_only.returncode, 0, blank_only.stderr)

                result = self.run_lib(
                    'production="$(printf \'%s\' "$PRODUCTION_IDS" | normalize_line_set)"; '
                    'staging="$(printf \'%s\' "$STAGING_IDS" | normalize_line_set)"; '
                    'require_disjoint_resource_ids "$RESOURCE_KIND" "$production" "$staging"',
                    env={
                        "RESOURCE_KIND": kind,
                        "PRODUCTION_IDS": "\nprod-only\n\n",
                        "STAGING_IDS": "\nstaging-only\n\n",
                    },
                )
                self.assertEqual(result.returncode, 0, result.stderr)

                overlap = self.run_lib(
                    'production="$(printf \'%s\' "$PRODUCTION_IDS" | normalize_line_set)"; '
                    'staging="$(printf \'%s\' "$STAGING_IDS" | normalize_line_set)"; '
                    'require_disjoint_resource_ids "$RESOURCE_KIND" "$production" "$staging"',
                    env={
                        "RESOURCE_KIND": kind,
                        "PRODUCTION_IDS": "\nshared\nprod-only\n",
                        "STAGING_IDS": "\nstaging-only\nshared\n",
                    },
                )
                self.assertNotEqual(overlap.returncode, 0)
                self.assertIn("identities overlap", overlap.stderr)

    def test_docker_resource_collectors_fail_closed_per_inspect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake_bin = Path(directory) / "bin"
            fake_bin.mkdir()
            log = Path(directory) / "docker.log"
            docker = fake_bin / "docker"
            docker.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                'id="${!#}"\n'
                'printf \'%s\\n\' "$id" >> "$STAGING_TEST_LOG"\n'
                'if [[ "$id" == "${STAGING_TEST_FAIL_ID:-}" ]]; then\n'
                "  exit 23\n"
                "fi\n"
                'if [[ "$*" == *NetworkSettings.Networks* ]]; then\n'
                '  printf \'\\nnet-%s\\nnet-%s\\n\\n\' "$id" "$id"\n'
                "else\n"
                '  printf \'\\nvolume-%s\\nvolume-%s\\n\\n\' "$id" "$id"\n'
                "fi\n"
            )
            docker.chmod(0o755)
            base_env = {
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "STAGING_TEST_LOG": str(log),
            }

            success_cases = (
                (
                    "network",
                    "collect_container_network_ids first middle final",
                    "net-final\nnet-first\nnet-middle\n",
                ),
                (
                    "volume",
                    "collect_container_volume_names first middle final",
                    "volume-final\nvolume-first\nvolume-middle\n",
                ),
            )
            for name, command, expected in success_cases:
                with self.subTest(success=name):
                    log.write_text("")
                    result = self.run_lib(command, env=base_env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, expected)
                    self.assertEqual(
                        log.read_text().splitlines(),
                        ["first", "middle", "final"],
                    )

            failure_cases = (
                (
                    "Production network first",
                    "collect_container_network_ids first middle final",
                    "first",
                    ["first"],
                ),
                (
                    "Production network middle",
                    "collect_container_network_ids first middle final",
                    "middle",
                    ["first", "middle"],
                ),
                (
                    "Production network final",
                    "collect_container_network_ids first middle final",
                    "final",
                    ["first", "middle", "final"],
                ),
                (
                    "Staging network first",
                    "collect_container_network_ids first second",
                    "first",
                    ["first"],
                ),
                (
                    "Staging network second",
                    "collect_container_network_ids first second",
                    "second",
                    ["first", "second"],
                ),
                (
                    "Production volume first",
                    "collect_container_volume_names first middle final",
                    "first",
                    ["first"],
                ),
                (
                    "Production volume middle",
                    "collect_container_volume_names first middle final",
                    "middle",
                    ["first", "middle"],
                ),
                (
                    "Production volume final",
                    "collect_container_volume_names first middle final",
                    "final",
                    ["first", "middle", "final"],
                ),
                (
                    "Staging volume inspect",
                    "collect_container_volume_names first second",
                    "second",
                    ["first", "second"],
                ),
            )
            for name, command, failed_id, expected_calls in failure_cases:
                with self.subTest(failure=name):
                    log.write_text("")
                    result = self.run_lib(
                        command,
                        env={**base_env, "STAGING_TEST_FAIL_ID": failed_id},
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(
                        log.read_text().splitlines(), expected_calls
                    )
                    self.assertIn("failed to inspect container", result.stderr)

    def test_api_readiness_wait_is_bounded_and_fail_closed(self) -> None:
        def run_wait(
            states: str,
            *,
            attempts: int = 3,
            discovery_failure: bool = False,
            inspect_failure_call: int = 0,
        ):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                fake_bin = root / "bin"
                fake_bin.mkdir()
                counter = root / "inspect-count"
                docker = fake_bin / "docker"
                docker.write_text(
                    "#!/usr/bin/env bash\n"
                    "set -euo pipefail\n"
                    'case "${1:-}" in\n'
                    "  ps)\n"
                    '    [[ "${STAGING_TEST_DISCOVERY_FAILURE:-0}" != 1 ]] || exit 21\n'
                    "    printf 'api-container\\n'\n"
                    "    ;;\n"
                    "  inspect)\n"
                    "    count=0\n"
                    '    [[ ! -f "$STAGING_TEST_COUNTER" ]] || count="$(<"$STAGING_TEST_COUNTER")"\n'
                    "    count=$((count + 1))\n"
                    '    printf \'%s\\n\' "$count" > "$STAGING_TEST_COUNTER"\n'
                    '    [[ "$count" != "${STAGING_TEST_INSPECT_FAILURE_CALL:-0}" ]] || exit 22\n'
                    "    IFS=',' read -r -a states <<< \"$STAGING_TEST_STATES\"\n"
                    "    index=$((count - 1))\n"
                    "    (( index < ${#states[@]} )) || index=$((${#states[@]} - 1))\n"
                    "    printf '%s\\n' \"${states[$index]}\"\n"
                    "    ;;\n"
                    "  *) exit 23 ;;\n"
                    "esac\n"
                )
                docker.chmod(0o755)
                result = self.run_lib(
                    f"wait_container_healthy maoxx-staging api {attempts} 0",
                    env={
                        "PATH": f"{fake_bin}:{os.environ['PATH']}",
                        "STAGING_TEST_COUNTER": str(counter),
                        "STAGING_TEST_STATES": states,
                        "STAGING_TEST_DISCOVERY_FAILURE": (
                            "1" if discovery_failure else "0"
                        ),
                        "STAGING_TEST_INSPECT_FAILURE_CALL": str(
                            inspect_failure_call
                        ),
                    },
                )
                count = int(counter.read_text()) if counter.exists() else 0
                return result, count

        cases = (
            (
                "starting then healthy",
                "running|starting,running|healthy",
                0,
                2,
            ),
            ("healthy immediately", "running|healthy", 0, 1),
            (
                "starting then unhealthy",
                "running|starting,running|unhealthy",
                1,
                2,
            ),
            ("container exits", "running|starting,exited|starting", 1, 2),
        )
        for name, states, expected_status, expected_inspects in cases:
            with self.subTest(state_sequence=name):
                result, count = run_wait(states)
                self.assertEqual(result.returncode == 0, expected_status == 0)
                self.assertEqual(count, expected_inspects)

        inspect_failure, count = run_wait(
            "running|starting,running|healthy", inspect_failure_call=1
        )
        self.assertNotEqual(inspect_failure.returncode, 0)
        self.assertEqual(count, 1)
        self.assertIn("failed to inspect", inspect_failure.stderr)

        discovery_failure, count = run_wait(
            "running|healthy", discovery_failure=True
        )
        self.assertNotEqual(discovery_failure.returncode, 0)
        self.assertEqual(count, 0)
        self.assertIn("failed to", discovery_failure.stderr)

        timeout, count = run_wait("running|starting", attempts=3)
        self.assertNotEqual(timeout.returncode, 0)
        self.assertEqual(count, 3)
        self.assertIn("timed out", timeout.stderr)

    def test_verification_runs_only_after_api_readiness(self) -> None:
        deploy = (ROOT / "scripts/staging/deploy.sh").read_text()
        self.assertLess(
            deploy.index('"${COMPOSE[@]}" up -d api'),
            deploy.index('wait_container_healthy "$STAGING_PROJECT" api'),
        )
        self.assertLess(
            deploy.index('wait_container_healthy "$STAGING_PROJECT" api'),
            deploy.index(
                "run_staging_verification",
                deploy.index("start_staging_api_and_verify"),
            ),
        )

        for readiness_status in (0, 1):
            with (
                self.subTest(readiness_status=readiness_status),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                fake_bin = root / "bin"
                fake_bin.mkdir()
                log = root / "order.log"
                docker = fake_bin / "docker"
                docker.write_text(
                    "#!/usr/bin/env bash\n"
                    'printf \'docker:%s\\n\' "$*" >> "$STAGING_TEST_LOG"\n'
                )
                docker.chmod(0o755)
                result = subprocess.run(
                    [
                        "bash",
                        "-c",
                        f'source "{ROOT / "scripts/staging/deploy.sh"}"; '
                        'wait_container_healthy() { printf \'readiness\\n\' >> "$STAGING_TEST_LOG"; return "$STAGING_TEST_READINESS_STATUS"; }; '
                        "run_staging_verification() { printf 'verify\\n' >> \"$STAGING_TEST_LOG\"; }; "
                        "start_staging_api_and_verify",
                    ],
                    env={
                        **os.environ,
                        "PATH": f"{fake_bin}:{os.environ['PATH']}",
                        "STAGING_TEST_LOG": str(log),
                        "STAGING_TEST_READINESS_STATUS": str(readiness_status),
                    },
                    capture_output=True,
                    text=True,
                )
                calls = log.read_text().splitlines()
                self.assertTrue(calls[0].endswith(" up -d api"))
                self.assertEqual(calls[1], "readiness")
                if readiness_status == 0:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(calls[2:], ["verify"])
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(calls[2:], [])

    def test_partial_compose_up_failure_runs_safe_down(self) -> None:
        for resource in ("container", "network", "volume"):
            with (
                self.subTest(resource=resource),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                fake_bin = root / "bin"
                fake_bin.mkdir()
                log = root / "docker.log"
                docker = fake_bin / "docker"
                docker.write_text(
                    "#!/usr/bin/env bash\n"
                    'printf \'%s\\n\' "$*" >> "$STAGING_TEST_LOG"\n'
                    'case "$*" in\n'
                    "  'ps -aq --filter label=com.docker.compose.project=maoxx-staging') [[ \"$STAGING_FAKE_RESOURCE\" != container ]] || echo partial-container; exit 0 ;;\n"
                    "  'network ls -q --filter label=com.docker.compose.project=maoxx-staging') [[ \"$STAGING_FAKE_RESOURCE\" != network ]] || echo partial-network; exit 0 ;;\n"
                    "  'volume ls -q --filter label=com.docker.compose.project=maoxx-staging') [[ \"$STAGING_FAKE_RESOURCE\" != volume ]] || echo partial-volume; exit 0 ;;\n"
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
                        "STAGING_FAKE_RESOURCE": resource,
                    },
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 17)
                calls = log.read_text().splitlines()
                self.assertTrue(
                    any(call.endswith(" up -d db") for call in calls)
                )
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

    def test_staging_resource_query_failure_is_not_silent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake_bin = Path(directory) / "bin"
            fake_bin.mkdir()
            docker = fake_bin / "docker"
            docker.write_text(
                '#!/usr/bin/env bash\nif [[ "$1" == ps ]]; then exit 23; fi\n'
            )
            docker.chmod(0o755)
            result = self.run_lib(
                "staging_project_resources_exist",
                env={"PATH": f"{fake_bin}:{os.environ['PATH']}"},
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("failed to query staging containers", result.stderr)

    def test_approved_git_archive_excludes_mutable_inputs_and_drives_build(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            repo = base / "repo"
            temp_root = base / "tmp"
            fake_bin = base / "bin"
            repo.mkdir()
            temp_root.mkdir()
            fake_bin.mkdir()
            target = self.make_critical_git_tree(repo)
            original_dockerfile = (repo / "Dockerfile").read_text()
            for name in (
                "app/injected.sql",
                "alembic/private.dump",
                "app/config.staging.local",
                ".env.staging",
                "storage/untracked.bin",
            ):
                path = repo / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("ignored mutable input\n")
            clean = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(clean.stdout, "")
            validated = self.run_lib(
                'require_approved_checkout "$TARGET_SHA" "$TEST_ROOT"',
                env={"TARGET_SHA": target, "TEST_ROOT": str(repo)},
            )
            self.assertEqual(validated.returncode, 0, validated.stderr)
            created = self.run_lib(
                'create_approved_build_context "$TARGET_SHA" "$TEST_ROOT"',
                env={
                    "TARGET_SHA": target,
                    "TEST_ROOT": str(repo),
                    "TMPDIR": str(temp_root),
                },
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            context = Path(created.stdout.strip())
            self.assertTrue(context.is_dir())
            self.assertEqual(context.stat().st_mode & 0o222, 0)
            for name in (
                "Dockerfile",
                "app",
                "alembic",
                "alembic.ini",
                "requirements.txt",
            ):
                self.assertTrue((context / name).exists(), name)
            for name in (
                ".git",
                ".env.staging",
                "storage",
                "app/injected.sql",
                "alembic/private.dump",
                "app/config.staging.local",
            ):
                self.assertFalse((context / name).exists(), name)

            (repo / "Dockerfile").write_text("mutated after validation\n")
            self.assertEqual(
                (context / "Dockerfile").read_text(), original_dockerfile
            )
            log = base / "docker.log"
            docker = fake_bin / "docker"
            docker.write_text(
                "#!/usr/bin/env bash\n"
                'printf \'%s\\n\' "$*" > "$STAGING_TEST_LOG"\n'
            )
            docker.chmod(0o755)
            built = self.run_lib(
                'build_approved_image "$CONTEXT" "maoxx-os-staging-api:$TARGET_SHA"',
                env={
                    "CONTEXT": str(context),
                    "TARGET_SHA": target,
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "STAGING_TEST_LOG": str(log),
                },
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            build_args = log.read_text().strip()
            self.assertIn(f"--file {context}/Dockerfile", build_args)
            self.assertTrue(build_args.endswith(str(context)))
            self.assertNotIn("/opt/maoxx-os-staging", build_args)

            removed = self.run_lib(
                'remove_approved_build_context "$CONTEXT"',
                env={"CONTEXT": str(context), "TMPDIR": str(temp_root)},
            )
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertFalse(context.exists())

    def test_approved_context_is_cleaned_when_build_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            repo = base / "repo"
            temp_root = base / "tmp"
            fake_bin = base / "bin"
            repo.mkdir()
            temp_root.mkdir()
            fake_bin.mkdir()
            target = self.make_critical_git_tree(repo)
            docker = fake_bin / "docker"
            docker.write_text("#!/usr/bin/env bash\nexit 19\n")
            docker.chmod(0o755)
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    f'source "{ROOT / "scripts/staging/deploy.sh"}"; '
                    'approved_context="$(create_approved_build_context "$TARGET_SHA" "$TEST_ROOT")"; '
                    "arm_deploy_cleanup; "
                    'build_approved_image "$approved_context" "maoxx-os-staging-api:$TARGET_SHA"',
                ],
                env={
                    **os.environ,
                    "TARGET_SHA": target,
                    "TEST_ROOT": str(repo),
                    "TMPDIR": str(temp_root),
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                },
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 19)
            self.assertEqual(list(temp_root.iterdir()), [])

    def test_invalid_staging_database_urls_are_rejected(self) -> None:
        example = (ROOT / ".env.staging.example").read_text()
        replacements = (
            ("@db:5432/", "@production-db:5432/"),
            ("//maoxx_staging_example:", "//wrong_user:"),
            ("/maoxx_staging_example\n", "/wrong_database\n"),
        )
        for old, new in replacements:
            with (
                self.subTest(replacement=new),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                subprocess.run(["git", "init", "-q"], cwd=root, check=True)
                env_file = root / ".env.staging"
                env_file.write_text(example.replace(old, new))
                env_file.chmod(0o600)
                result = self.run_lib(
                    'validate_env_file "$ENV_FILE" "$TEST_ROOT"',
                    env={"ENV_FILE": str(env_file), "TEST_ROOT": str(root)},
                )
                self.assertNotEqual(result.returncode, 0)

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
        documents = {
            name: (ROOT / "docs" / name).read_text()
            for name in (
                "ROADMAP.md",
                "PROJECT_STATUS.md",
                "PHASE_1D_CONTROL_PLANE.md",
                "CHANGELOG.md",
                "STAGING.md",
                "PHASE_1D_E_SUPERVISION.md",
            )
        }
        combined = "\n".join(documents.values())
        self.assertIn("Phase 1D-D", combined)
        self.assertIn("accepted", combined)
        self.assertIn("Phase 1D-E", combined)
        self.assertIn("implemented_pending_verification", combined)
        self.assertIn("in_progress", combined)
        self.assertIn("not_started", combined)

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
