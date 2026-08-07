# Codex Cloud repository workflow

## Validation status

Phase 1D-C is `accepted`. The repository workflow was published from branch `codex/implement-phase-1d-c-codex-cloud-validation` and successfully created and updated Pull Request #8. The latest `pull_request` synchronize event automatically triggered CI, and all five jobs, including the required `CI / Quality Gate`, passed. Phase 1D remains `in_progress`; this work does not enter Phase 1D-D or Phase 2A.

## GitHub connection and repository scope

Codex Cloud is connected to GitHub for the single authorized repository `Mxx1233/Maoxx-OS`. Repository access is limited to that repository and does not authorize access to other repositories, infrastructure, databases, storage, Feishu configuration, or Production resources.

Codex Cloud work must use a task branch and a Pull Request targeting `main`. It must not write directly to `main`, approve its own Pull Request, merge automatically, bypass the repository Ruleset, or weaken review requirements.

## Cloud environment boundary

The Codex Cloud environment is non-production and is used only for repository development and validation. Agent internet access is disabled. The environment contains no Secrets, production credentials, production `.env` values, deployment credentials, database backups, user media, or production configuration.

Repository tasks must not request, invent, print, or configure GitHub credentials, tokens, personal access tokens, SSH keys, application Secrets, or production identifiers. They must not access Production resources.

## Read-only validation

A successful read-only Codex Cloud validation task:

1. opened `AGENTS.md` and the mandatory project documents;
2. inspected repository status and governance boundaries without changing the repository;
3. confirmed the authorized repository scope and non-production environment constraints; and
4. completed with zero file diff.

The zero-diff result demonstrates that Codex Cloud can read the repository instructions without making an unrequested change. It does not grant permission to bypass the task-branch, Pull Request, CI, or review workflow.

## Publication and CI evidence

Codex Cloud successfully published branch `codex/implement-phase-1d-c-codex-cloud-validation`, created Pull Request #8 targeting `main`, and updated the same Pull Request without creating another branch or PR. The latest PR synchronize event automatically triggered `CI / Quality`, `CI / Unit Tests`, `CI / PostgreSQL Integration`, `CI / Docker Build`, and `CI / Quality Gate`; all five jobs passed, including the required gate.

All Pull Requests, including documentation-only Pull Requests, are subject to the required `CI / Quality Gate`. No CI requirement was bypassed. Codex Cloud did not write directly to `main`, automatically approve or merge Pull Request #8, or access any Production resource.

## Existing governance boundaries

The existing GitHub governance remains authoritative:

- changes use a task branch and one Pull Request targeting `main`;
- the active `Protect main` Ruleset prohibits direct push, force push, and branch deletion and requires the Pull Request workflow and conversation resolution;
- `CI / Quality Gate` is the only configured required status check;
- independent API tests, secret scanning, and destructive migration scanning are not currently configured and must not be represented as active controls;
- Codex Cloud must not approve or merge its own Pull Request; and
- Production deployment, credentials, and resources are outside Phase 1D-C.

The broader control-plane status and remaining boundaries are documented in [Phase 1D Cloud Development Control Plane](PHASE_1D_CONTROL_PLANE.md). Project phase status is recorded in the [roadmap](ROADMAP.md) and [project status](PROJECT_STATUS.md).

## Verification and rollback

Phase 1D-C is accepted based on the configured repository connection and restricted permission, non-production Cloud boundary, zero-diff read-only validation, successful branch and Pull Request workflow, and passing required CI gate. Acceptance does not authorize automatic approval or merge and does not begin Phase 1D-D.

This phase changes documentation only. Rollback is a normal Git revert of the documentation commit; no database, migration, application, Docker Compose, GitHub Actions, production, or Feishu rollback is required.
