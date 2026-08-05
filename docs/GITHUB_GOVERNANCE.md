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
- Required status checks: waiting for the first Phase 1D-B CI run; after it
  succeeds, the Ruleset must require only `CI / Quality Gate`.
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

## Phase 1D-A acceptance

Phase 1D-A is `accepted`. Its evidence includes:

- independent task branches and Pull Requests #2, #3 and #4;
- the Pull Request template and Codex task Issue template in `main`;
- CODEOWNERS resolves to `@Mxx1233` for changed paths;
- the active ruleset proves deletion/force-push restrictions, required PR, conversation resolution, and empty bypass;
- normal non-destructive evidence proves direct main push is rejected;
- all Phase 1D-A changes merged through Pull Requests rather than direct push.

## Phase 1D-B CI gate

Phase 1D-B is `implemented_pending_verification` on its task branch. The `CI`
workflow runs on Pull Requests targeting `main`, pushes to `main`, and manual
dispatch. It grants only `contents: read`, does not persist checkout
credentials, and has no deployment, package, Pull Request, or OIDC write
permission.

The workflow uses these stable job names:

- `CI / Quality`
- `CI / Unit Tests`
- `CI / PostgreSQL Integration`
- `CI / Docker Build`
- `CI / Quality Gate`

Only `CI / Quality Gate` is intended as a required Ruleset check. It succeeds
only when all four execution jobs succeed. The user must enable that check only
after its first successful run, then verify that a failing dependency blocks
the gate and merge.

CI uses only fixed non-production placeholders and an ephemeral PostgreSQL 16
service container. It does not read Production `.env`, use repository Secrets,
contact the Production server, send Feishu messages, call an LLM, push an
image, or deploy. Phase 1D-B remains pending until the workflow and Ruleset are
verified in the Pull Request.
