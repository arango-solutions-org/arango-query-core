---
name: pr-review
description: Review an open arango-query-core pull request end to end — independently verify mergeability and the full CI gate (pytest, ruff, mypy) in an isolated worktree, trace the diff for correctness, and report a clear merge/no-merge recommendation. Use when asked to look at, review, or check a PR in this repo, or before merging one.
---

# PR review (arango-query-core)

## Invocation
`/pr-review <PR number>` — or just ask to look at / review a PR.

## Purpose
Don't trust GitHub's green checkmark or a skim of the diff alone. Independently
rebuild the PR's test/lint/type gate in a scratch worktree, trace the tricky
logic by hand against the new tests, and only then recommend merging. This repo
has hit real, silent failure modes (see **Gotchas** below) that a surface-level
review misses.

## Repo shape (read this first)
- **Primary repo:** `arango-solutions-org/arango-query-core` — PRs and issues
  live here. **Secondary/mirror:** `ArthurKeen/arango-query-core` — code-only,
  kept in sync by a dual-push `origin` remote; it does not carry its own PRs.
  Always run `gh pr` / `gh issue` commands against the primary unless told
  otherwise. (Confirm which is primary — it has changed before.)
- **Release gate:** PyPI publishing is pinned-artifact (CC-9): every merge to
  `main` is a potential version bump. If the PR's `CHANGELOG.md` entry lands
  under a version header that's already tagged and published, that's a bug in
  the PR (or in a prior release) worth flagging, not something to wave through.

## Protocol

### 1. Pull the PR's shape
```bash
gh pr view <N> --repo arango-solutions-org/arango-query-core \
  --json title,body,author,headRefName,baseRefName,commits,additions,deletions,changedFiles,mergeable,statusCheckRollup
gh pr diff <N> --repo arango-solutions-org/arango-query-core
```
Read the PR body as the author's own claim about scope and evidence (eval
numbers, measured deltas) — you're about to check whether the code backs it up.

### 2. Independently verify mergeability
Don't trust the `mergeable` field alone — it can read `UNKNOWN` right after a
push, and (per the gotchas below) a corrupted commit graph can make GitHub
report `CONFLICTING` even when the content is actually fine, or vice versa.
Cross-check with a real history-aware merge:
```bash
git fetch origin
git merge-tree --write-tree origin/main origin/<head-branch>
echo "exit: $?"   # 0 and a single tree id = clean; conflict markers = real conflict
```
Use the **modern 3-ref-aware form** (`--write-tree`), never the bare legacy
`git merge-tree <a> <b>` — it ignores actual commit ancestry and reports false
conflicts for a branch that already contains `main`.

### 3. Rebuild the CI gate in an isolated worktree
Never run this against your own working tree (it may have unrelated local
changes, and installing a PR's deps into your main venv is how versions
drift). Mirror `.github/workflows/ci.yml` exactly:
```bash
git worktree add /tmp/pr<N>-check origin/<head-branch> --detach
cd /tmp/pr<N>-check
python3 -m venv .venv-check && source .venv-check/bin/activate
pip install -q -e ".[dev]"

pytest -q                    # mirrors: pytest -q --tb=short
ruff check .
ruff format --check .
mypy                          # mirrors: mypy (files = ["arango_query_core"])
```
All four must be clean. Note the test count and compare it to `main`'s —
a PR that doesn't grow the count for a new feature is under-tested.

Clean up when done:
```bash
cd /Users/arthurkeen/code/arango-query-core
git worktree remove /tmp/pr<N>-check --force
```

### 4. Trace the diff, don't just skim it
For anything touching control flow (retry loops, fallback logic, ordering
rules), hand-trace at least the two or three trickiest test cases against the
actual code — don't assume the tests passing proves the stated semantics.
Specifically check:
- **Scope honesty** — does the PR body list what's *not* included? A PR that
  quietly expands scope beyond its own description is a flag, not a bonus.
- **Protocol/seam changes** — `QueryLanguageAdapter` seams are called
  *unconditionally* by `NLQueryEngine` (no `hasattr` guard). A new seam means
  every existing `FakeAdapter` in the test suite needs a stub for it, or the
  merged tree will crash at prompt-build time. Grep for adapter classes in
  `tests/` and confirm each implements every current seam.
- **CHANGELOG placement** — does the entry land under a version header that's
  already tagged (`git tag -l`) and already on PyPI? If so, the version is
  effectively lying about its own contents (see Repo shape above) — flag it
  even though it's not this PR's fault to fix.

### 5. Report
State plainly: what the PR does, the hand-traced correctness notes, the
independent verification results (test count, ruff, mypy, merge-tree exit
code, CI check conclusions), any scope/changelog issues found, and an explicit
recommend-or-not. Don't hedge if everything checks out — say so and recommend
merging.

### 6. If asked to merge
```bash
gh pr merge <N> --repo arango-solutions-org/arango-query-core --merge --delete-branch
```
Re-check `mergeable`/CI immediately before merging — state can change between
review and merge. After merging, **sync manually** — merging on GitHub does
not update your local clone or the mirror:
```bash
git fetch origin && git checkout main && git merge --ff-only origin/main
git push origin main   # dual-push: lands on both primary and mirror
```
`--delete-branch` only deletes the branch on the repo the PR lives in
(primary). If the branch was ever pushed to the mirror too, delete it there
explicitly: `git push https://github.com/ArthurKeen/arango-query-core.git --delete <branch>`.

## Gotchas (hard-won, don't re-learn these)

- **Never `git stash` mid-merge-conflict-resolution**, even for an unrelated
  side-check (like "did this formatting issue pre-exist on main?"). Stashing
  while `MERGE_HEAD` is set but conflicts are already resolved-and-staged
  silently drops the merge state — the commit you make afterward looks fine
  (correct file content) but has only **one parent**, so Git and GitHub don't
  actually see it as containing `main`. Symptom: `mergeable` stays
  `CONFLICTING` after a resolution that looks complete. Fix: check
  `git log --format='%H %P' <commit>` — a merge commit must show two parents.
  If it shows one, `git reset --hard` to before the merge and redo it cleanly
  with no stash in between, then verify parent count *before* pushing.
- **A clean-looking commit can still get zero CI runs.** Twice now, a push
  (once a corrupted single-parent "merge", once a plain version-bump commit
  plus its tag) registered **no check-suite at all** — not even third-party
  apps like GitGuardian. This isn't a code problem; it's GitHub event-delivery
  flakiness. Verify with
  `gh api repos/<repo>/commits/<sha>/check-suites --jq '.check_suites'`
  before concluding anything is broken. A delete-and-recreate of a tag
  sometimes forces redelivery; it isn't guaranteed to. Don't block a release
  on it — fall back to the proven manual path (below) rather than debug
  GitHub's webhook plumbing indefinitely.
- **PyPI publish fallback.** The `publish.yml` workflow uses trusted
  publishing (OIDC, no token) gated to `github.repository ==
  'ArthurKeen/arango-query-core'` and only fires on a `v*` tag push. If it
  doesn't fire (see above) or you need to publish immediately: build and
  check locally, then upload with the token in the gitignored `.env`:
  ```bash
  python -m build --outdir <scratch>/dist
  twine check <scratch>/dist/*
  TWINE_USERNAME=__token__ TWINE_PASSWORD=$(grep '^PYPI_TOKEN=' .env | cut -d= -f2) \
    twine upload --non-interactive <scratch>/dist/*
  ```
  If the workflow does fire later for a version already manually published,
  it will just hit a harmless `400 File already exists` on the duplicate
  filename — not a problem, and actually confirms trusted publishing works.
- **PyPI's project JSON can lag a few seconds after upload** — `info.version`
  may show the previous release momentarily. Re-check, or check
  `'<version>' in releases` / hit the version's own release-page URL directly,
  before concluding an upload failed.
