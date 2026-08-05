# GitHub Development Governance

## Purpose

GitHub is the controlled development entry point for Maoxx OS. Changes must originate from an approved task, use an independent branch, pass review and later CI gates, and merge through a Pull Request.

## Current main ruleset

User-verified GitHub configuration on 2026-08-05:

- Repository visibility: Public.
- Ruleset: `Protect main`, Active.
- Target: default branch (`main`).
- Restrict deletions: enabled.
- Block force pushes: enabled.
- Require a pull request before merging: enabled.
- Required approvals: 0 during Phase 1D-A.
- Require conversation resolution before merging: enabled.
- Require CODEOWNERS review: disabled during Phase 1D-A.
- Required status checks: not yet enabled; Phase 1D-B will add CI first.
- Bypass list: empty.

Direct push, force push, branch deletion, and bypass attempts against `main` are prohibited. Do not test deletion or force-push protection by performing destructive operations; verify them from the active ruleset and normal PR behavior.

## Required workflow

1. Open a Codex task Issue with phase, scope, exclusions, acceptance criteria, validation, rollback, and approvals.
2. Read `AGENTS.md` and required project documents, produce a plan, and wait for approval.
3. Refresh `origin/main`, then create an independent `task/`, `fix/`, or `test/` branch.
4. Never develop, commit, or push directly on `main`.
5. Implement only the approved phase and scope, then run local validation.
6. Push only the task branch and open a PR targeting `main`.
7. Resolve every review conversation. Migration changes require explicit human review even while required approvals remain zero.
8. Merge only after current required checks and approvals pass. Production deployment is a separate approval and never follows automatically from merge.

## Migration review

Every migration PR must identify the revision, affected objects, locking/data risks, backup requirement, compatibility window, and rollback or forward-fix strategy. Human review must search for `drop_table`, `drop_column`, `drop_constraint`, `drop_index`, destructive/narrowing `alter_column`, and raw SQL that irreversibly deletes or rewrites data.

An executed migration is immutable. Destructive work requires a verified backup, isolated database test, explicit approval, and production maintenance decision. Alembic downgrade is never an automatic rollback mechanism.

## CODEOWNERS and reviews

`@Mxx1233` owns the complete repository, with explicit entries for GitHub governance, Alembic, database models, Compose, Dockerfile, ADRs, and `AGENTS.md`. CODEOWNERS review is not yet enforced by the ruleset and must not be claimed as required.

## Phase 1D-A verification

Phase 1D-A remains `implemented_pending_verification` until:

- the test branch is pushed successfully;
- a Pull Request targeting `main` exists and displays the PR template;
- CODEOWNERS resolves to `@Mxx1233` for changed paths;
- the active ruleset proves deletion/force-push restrictions, required PR, conversation resolution, and empty bypass;
- normal non-destructive evidence proves direct main push is rejected;
- the PR remains unmerged during verification.

Phase 1D-B will add required status checks only after CI is separately designed and approved.
