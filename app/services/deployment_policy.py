import re
from dataclasses import dataclass
from enum import StrEnum


DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IMMUTABLE_IMAGE_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/:\-]*@sha256:[0-9a-f]{64}$"
)
APPROVED_PRODUCTION_SERVICES = frozenset({"api", "feishu-worker"})


class DeploymentPolicyError(ValueError):
    pass


class MigrationRisk(StrEnum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


LOW_MIGRATION_CHANGES = frozenset(
    {
        "add_table",
        "add_nullable_column",
        "add_nonblocking_index",
        "add_constraint_not_valid",
    }
)
MEDIUM_MIGRATION_CHANGES = frozenset(
    {
        "reversible_data_backfill",
        "reversible_column_type",
        "validated_constraint",
        "blocking_index",
    }
)
HIGH_MIGRATION_CHANGES = frozenset(
    {
        "drop_table",
        "drop_column",
        "destructive_data_change",
        "irreversible_change",
        "breaking_type_change",
        "exclusive_lock",
        "set_not_null_with_rewrite",
    }
)


def classify_migration_risk(changes: tuple[str, ...]) -> MigrationRisk:
    if not changes:
        return MigrationRisk.NONE
    change_set = frozenset(changes)
    if change_set & HIGH_MIGRATION_CHANGES:
        return MigrationRisk.HIGH
    known = LOW_MIGRATION_CHANGES | MEDIUM_MIGRATION_CHANGES
    if not change_set <= known:
        return MigrationRisk.HIGH
    if change_set & MEDIUM_MIGRATION_CHANGES:
        return MigrationRisk.MEDIUM
    return MigrationRisk.LOW


def validate_migration_plan(
    risk: MigrationRisk,
    *,
    migration_revision: str | None,
    rollback_runbook_ref: str | None,
) -> None:
    if risk is MigrationRisk.HIGH:
        raise DeploymentPolicyError("HIGH migration risk is prohibited")
    if risk is MigrationRisk.MEDIUM and (
        not migration_revision or not rollback_runbook_ref
    ):
        raise DeploymentPolicyError(
            "MEDIUM migration requires revision and rollback runbook"
        )


def validate_immutable_artifact(*, digest: str, image_reference: str) -> None:
    if not DIGEST_PATTERN.fullmatch(digest):
        raise DeploymentPolicyError("invalid artifact digest")
    if not IMMUTABLE_IMAGE_PATTERN.fullmatch(image_reference):
        raise DeploymentPolicyError("mutable image reference is forbidden")
    if image_reference.rsplit("@", 1)[1] != digest:
        raise DeploymentPolicyError("image reference digest mismatch")


def validate_service_set(services: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(sorted(set(services)))
    if not normalized or len(normalized) != len(services):
        raise DeploymentPolicyError("service set must be non-empty and unique")
    if not set(normalized) <= APPROVED_PRODUCTION_SERVICES:
        raise DeploymentPolicyError("service is outside approved allowlist")
    return normalized


@dataclass(frozen=True)
class ResourceSample:
    elapsed_ms: int
    mem_available_bytes: int
    swap_free_bytes: int
    root_free_bytes: int
    docker_free_bytes: int


@dataclass(frozen=True)
class ResourceGateResult:
    passed: bool
    reason: str
    median_mem_available_bytes: int | None = None
    minimum_mem_available_bytes: int | None = None


def evaluate_resource_window(
    samples: tuple[ResourceSample, ...],
) -> ResourceGateResult:
    if len(samples) != 7:
        return ResourceGateResult(False, "sample_count")
    metrics = [sample.elapsed_ms for sample in samples] + [
        value
        for sample in samples
        for value in (
            sample.mem_available_bytes,
            sample.swap_free_bytes,
            sample.root_free_bytes,
            sample.docker_free_bytes,
        )
    ]
    if any(type(value) is not int or value < 0 for value in metrics):
        return ResourceGateResult(False, "malformed_metric")
    elapsed = [sample.elapsed_ms for sample in samples]
    if elapsed[0] > 1000:
        return ResourceGateResult(False, "first_sample_not_immediate")
    intervals = [right - left for left, right in zip(elapsed, elapsed[1:])]
    if any(interval < 4000 or interval > 6000 for interval in intervals):
        return ResourceGateResult(False, "sample_interval")
    if elapsed[-1] < 28000 or elapsed[-1] > 32000:
        return ResourceGateResult(False, "sample_span")

    memory = sorted(sample.mem_available_bytes for sample in samples)
    median = memory[3]
    minimum = memory[0]
    details = {
        "median_mem_available_bytes": median,
        "minimum_mem_available_bytes": minimum,
    }
    if median < 1_073_741_824:
        return ResourceGateResult(False, "median_memory", **details)
    if minimum < 1_006_632_960:
        return ResourceGateResult(False, "minimum_memory", **details)
    if any(sample.swap_free_bytes < 1_342_177_280 for sample in samples):
        return ResourceGateResult(False, "swap", **details)
    if any(sample.root_free_bytes < 5_368_709_120 for sample in samples):
        return ResourceGateResult(False, "root_filesystem", **details)
    if any(sample.docker_free_bytes < 5_368_709_120 for sample in samples):
        return ResourceGateResult(False, "docker_filesystem", **details)
    return ResourceGateResult(True, "passed", **details)


INITIAL_STATE = "__initial__"
DEPLOYMENT_TRANSITIONS = {
    INITIAL_STATE: frozenset({"INTENT_CREATED"}),
    "INTENT_CREATED": frozenset({"ARTIFACT_RECORDED"}),
    "ARTIFACT_RECORDED": frozenset({"STAGING_DEPLOYING"}),
    "STAGING_DEPLOYING": frozenset({"STAGING_VALIDATED", "STAGING_FAILED"}),
    "STAGING_VALIDATED": frozenset(
        {"PRODUCTION_APPROVAL_PENDING", "ROLLBACK_APPROVAL_PENDING"}
    ),
    "PRODUCTION_APPROVAL_PENDING": frozenset({"PRODUCTION_DEPLOYING"}),
    "PRODUCTION_DEPLOYING": frozenset(
        {"PRODUCTION_HEALTHY", "PRODUCTION_FAILED"}
    ),
    "ROLLBACK_APPROVAL_PENDING": frozenset({"ROLLBACK_DEPLOYING"}),
    "ROLLBACK_DEPLOYING": frozenset({"ROLLED_BACK", "ROLLBACK_FAILED"}),
}


def require_transition(from_state: str | None, to_state: str) -> None:
    key = INITIAL_STATE if from_state is None else from_state
    if to_state not in DEPLOYMENT_TRANSITIONS.get(key, frozenset()):
        raise DeploymentPolicyError(
            f"illegal deployment transition: {key} -> {to_state}"
        )
