"""Trusted host-local Production resource collector for Phase 1D-F."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Callable
from uuid import UUID, uuid4


MEM_MEDIAN_MIN = 1_073_741_824
MEM_SAMPLE_MIN = 1_006_632_960
SWAP_SAMPLE_MIN = 1_342_177_280
DISK_SAMPLE_MIN = 5 * 1024**3


class ResourceAuthorityError(RuntimeError):
    pass


@dataclass(frozen=True)
class TrustedResourceSample:
    resource_check_id: UUID
    deployment_id: UUID
    environment: str
    host_checkpoint: str
    checkpoint_nonce: UUID
    captured_at: datetime
    monotonic_seconds: float
    mem_available_bytes: int
    swap_free_bytes: int
    root_free_bytes: int
    docker_free_bytes: int


@dataclass(frozen=True)
class TrustedResourceResult:
    resource_check_id: UUID
    deployment_id: UUID
    checkpoint_nonce: UUID
    samples: tuple[TrustedResourceSample, ...]
    evaluated_at: datetime
    passed: bool
    median_mem_available_bytes: int
    minimum_mem_available_bytes: int
    minimum_swap_free_bytes: int
    minimum_root_free_bytes: int
    minimum_docker_free_bytes: int

    def require_fresh(self, now: datetime) -> None:
        if (
            not self.passed
            or now - self.samples[-1].captured_at > timedelta(seconds=15)
            or now - self.evaluated_at > timedelta(seconds=30)
        ):
            raise ResourceAuthorityError("resource_evidence_stale_or_failed")


class TrustedProductionResourceCollector:
    """Callers provide no samples, timestamps, result, nonce, or checkpoint."""

    def __init__(
        self,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._sleep = sleep
        self._monotonic = monotonic
        self._now = now
        self._run = run

    def collect(self, deployment_id: UUID) -> TrustedResourceResult:
        check_id = uuid4()
        nonce = uuid4()
        checkpoint = f"{socket.gethostname()}:pre-mutation"
        samples: list[TrustedResourceSample] = []
        for index in range(7):
            samples.append(
                self._sample(check_id, deployment_id, nonce, checkpoint)
            )
            if index < 6:
                self._sleep(5)
        return self._evaluate(tuple(samples))

    def _sample(
        self, check_id: UUID, deployment_id: UUID, nonce: UUID, checkpoint: str
    ) -> TrustedResourceSample:
        fields: dict[str, int] = {}
        try:
            with open("/proc/meminfo", encoding="ascii") as stream:
                for line in stream:
                    name, raw = line.split(":", 1)
                    if name in {"MemAvailable", "SwapFree"}:
                        parts = raw.split()
                        if len(parts) != 2 or parts[1] != "kB":
                            raise ResourceAuthorityError("malformed_meminfo")
                        fields[name] = int(parts[0]) * 1024
        except (OSError, ValueError) as exc:
            raise ResourceAuthorityError("malformed_meminfo") from exc
        docker = self._run(
            ["docker", "info", "--format", "{{.DockerRootDir}}"],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
        docker_root = docker.stdout.strip()
        if (
            docker.returncode != 0
            or not docker_root
            or set(fields) != {"MemAvailable", "SwapFree"}
        ):
            raise ResourceAuthorityError("resource_metric_unavailable")
        return TrustedResourceSample(
            check_id,
            deployment_id,
            "production",
            checkpoint,
            nonce,
            self._now(),
            self._monotonic(),
            fields["MemAvailable"],
            fields["SwapFree"],
            shutil.disk_usage("/").free,
            shutil.disk_usage(os.path.realpath(docker_root)).free,
        )

    def _evaluate(
        self, samples: tuple[TrustedResourceSample, ...]
    ) -> TrustedResourceResult:
        if len(samples) != 7:
            raise ResourceAuthorityError("resource_sample_count")
        identity = {
            (
                sample.resource_check_id,
                sample.deployment_id,
                sample.environment,
                sample.host_checkpoint,
                sample.checkpoint_nonce,
            )
            for sample in samples
        }
        intervals = [
            samples[index].monotonic_seconds
            - samples[index - 1].monotonic_seconds
            for index in range(1, 7)
        ]
        span = samples[-1].monotonic_seconds - samples[0].monotonic_seconds
        if (
            len(identity) != 1
            or any(not 4 <= value <= 6 for value in intervals)
            or not 27 <= span <= 33
        ):
            raise ResourceAuthorityError("resource_cadence_or_binding")
        med = int(median(sample.mem_available_bytes for sample in samples))
        min_mem = min(sample.mem_available_bytes for sample in samples)
        min_swap = min(sample.swap_free_bytes for sample in samples)
        min_root = min(sample.root_free_bytes for sample in samples)
        min_docker = min(sample.docker_free_bytes for sample in samples)
        passed = (
            med >= MEM_MEDIAN_MIN
            and min_mem >= MEM_SAMPLE_MIN
            and min_swap >= SWAP_SAMPLE_MIN
            and min_root >= DISK_SAMPLE_MIN
            and min_docker >= DISK_SAMPLE_MIN
        )
        first = samples[0]
        return TrustedResourceResult(
            first.resource_check_id,
            first.deployment_id,
            first.checkpoint_nonce,
            samples,
            self._now(),
            passed,
            med,
            min_mem,
            min_swap,
            min_root,
            min_docker,
        )
