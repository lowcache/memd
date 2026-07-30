#!/usr/bin/env bash
# memd release: due-diligence gates, then the irreversible steps.
#
#   ./scripts/release.sh              # gates only, changes nothing (default)
#   ./scripts/release.sh --release    # gates, then push + tag + upload, each confirmed
#   ./scripts/release.sh --release --yes   # same, no prompts (CI / you are sure)
#
# The gates exist because a PyPI version cannot be replaced and a pushed tag is
# public immediately. Everything before RELEASE STEPS is read-only.
set -euo pipefail

cd "$(dirname "$0")/.."

DO_RELEASE=0
ASSUME_YES=0
EXPECT_BRANCH=main
ALLOW_BRANCH=0
for arg in "$@"; do
  case "$arg" in
    --release)      DO_RELEASE=1 ;;
    --yes|-y)       ASSUME_YES=1 ;;
    --allow-branch) ALLOW_BRANCH=1 ;;
    --help|-h)      sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

fail=0
step()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()    { printf '  \033[32mok\033[0m   %s\n' "$*"; }
bad()   { printf '  \033[31mFAIL\033[0m %s\n' "$*"; fail=1; }
warn()  { printf '  \033[33mwarn\033[0m %s\n' "$*"; }
confirm() {
  [ "$ASSUME_YES" = 1 ] && return 0
  printf '\n%s [y/N] ' "$1"
  read -r reply </dev/tty
  case "$reply" in [yY]*) return 0 ;; *) echo "aborted."; exit 1 ;; esac
}

# ---------------------------------------------------------------- versions
step "Version consistency"
VERSION=$(python3 - <<'PY'
import re, pathlib
src = pathlib.Path("memd/__init__.py").read_text()
print(re.search(r'^__version__ = "([^"]+)"', src, re.M).group(1))
PY
)
[ -n "$VERSION" ] || { echo "could not read __version__" >&2; exit 2; }
echo "  version under release: $VERSION"
TAG="v$VERSION"

if grep -q "^## \[$VERSION\]" CHANGELOG.md; then
  ok "CHANGELOG.md has a [$VERSION] section"
else
  bad "CHANGELOG.md has no '## [$VERSION]' section"
fi
if grep -q "badge/version-$VERSION-" README.md; then
  ok "README version badge matches"
else
  bad "README version badge is not $VERSION"
fi
if grep -q "^## \[Unreleased\]$" CHANGELOG.md; then
  # An Unreleased section with content means changes are about to ship unlogged.
  unreleased_body=$(awk '/^## \[Unreleased\]/{f=1;next} /^## \[/{f=0} f' CHANGELOG.md | tr -d '[:space:]')
  if [ -z "$unreleased_body" ]; then
    ok "Unreleased section is empty"
  else
    bad "CHANGELOG [Unreleased] still has content — move it under [$VERSION]"
  fi
fi

# ---------------------------------------------------------------- git state
step "Repository state"
BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$BRANCH" = "$EXPECT_BRANCH" ] || [ "$ALLOW_BRANCH" = 1 ]; then
  ok "on branch $BRANCH"
else
  bad "on branch '$BRANCH', expected '$EXPECT_BRANCH' (pass --allow-branch to override)"
fi

if [ -z "$(git status --porcelain)" ]; then
  ok "working tree clean"
else
  bad "working tree is dirty — commit or stash first"
fi
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  bad "tag $TAG already exists locally"
else
  ok "tag $TAG is free locally"
fi
if git ls-remote --exit-code --tags origin "$TAG" >/dev/null 2>&1; then
  bad "tag $TAG already exists on origin — bump the version"
else
  ok "tag $TAG is free on origin"
fi

# ---------------------------------------------- personal data (the 2026-07-23 incident)
step "Personal data not tracked"
tracked_private=$(git ls-files | grep -E '^\.(memory|model)/' || true)
if [ -z "$tracked_private" ]; then
  ok ".memory/ and .model/ are not tracked"
else
  bad "tracked personal files: $tracked_private"
fi
# ---------------------------------------------------------------- test suite
step "Test suite"
if python3 -m pytest tests/ -q >/tmp/memd-release-tests.log 2>&1; then
  ok "$(tail -1 /tmp/memd-release-tests.log)"
else
  bad "pytest failed — see /tmp/memd-release-tests.log"; tail -15 /tmp/memd-release-tests.log
fi

if command -v shellcheck >/dev/null 2>&1; then
  if shellcheck --shell=sh contrib/curators/*.sh >/dev/null 2>&1; then
    ok "shellcheck clean on contrib/curators"
  else
    bad "shellcheck reported problems in contrib/curators"
  fi
else
  warn "shellcheck not installed; skipped (CI runs it)"
fi

if command -v nix >/dev/null 2>&1; then
  warn "nix flake check not run here (slow); CI covers it — run manually if the flake changed"
else
  warn "nix not installed; flake check skipped"
fi

# ---------------------------------------------------------------- build
step "Build and inspect artifacts"
rm -rf dist build ./*.egg-info
if python3 -m build -q >/tmp/memd-release-build.log 2>&1; then
  ok "built sdist + wheel"
else
  bad "build failed — see /tmp/memd-release-build.log"
  tail -15 /tmp/memd-release-build.log
fi

if [ -d dist ]; then
  if python3 -m twine check dist/* >/dev/null 2>&1; then
    ok "twine check passed"
  else
    bad "twine check failed"
  fi
  for f in dist/*; do
    case "$f" in *"$VERSION"*) ;; *) bad "artifact does not carry version $VERSION: $f" ;; esac
  done

  # What actually ships. The July 2026 exposure was personal data inside a
  # published tree, so inspect the built artifacts rather than trusting
  # MANIFEST/packaging config.
  listing=$(mktemp)
  for f in dist/*.tar.gz; do tar tzf "$f"; done  >"$listing"
  for f in dist/*.whl;    do python3 -m zipfile -l "$f" | awk 'NR>2{print $1}'; done >>"$listing"

  if grep -qE '(^|/)\.(memory|model)/' "$listing"; then
    bad "artifacts contain .memory/ or .model/:"; grep -E '(^|/)\.(memory|model)/' "$listing" | head
  else
    ok "artifacts contain no .memory/ or .model/"
  fi
  if grep -qE '(^|/)\.(env|git)/' "$listing"; then
    bad "artifacts contain .env/ or .git/"
  else
    ok "artifacts contain no .env/ or .git/"
  fi

  # Absolute home paths and credential-shaped strings inside the sdist.
  tmpx=$(mktemp -d)
  tar xzf dist/*.tar.gz -C "$tmpx"
  if grep -rIl -e "$HOME" "$tmpx" >/dev/null 2>&1; then
    bad "sdist embeds absolute home paths:"; grep -rIl -e "$HOME" "$tmpx" | head
  else
    ok "sdist embeds no absolute home paths"
  fi
  # The redaction tests necessarily contain credential-SHAPED fixtures (all
  # synthetic: "x"*24 filler, AWS's documented AKIAIOSFODNN7EXAMPLE, truncated
  # PEM headers). Allowlist those two files by name so the check keeps its
  # teeth everywhere else rather than being switched off.
  CRED_RE='sk-ant-[A-Za-z0-9]|ghp_[A-Za-z0-9]{20}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----'
  hits=$(grep -rIlE "$CRED_RE" "$tmpx" 2>/dev/null \
         | sed "s|^$tmpx/[^/]*/||" \
         | grep -vxF -e tests/test_redact.py -e tests/test_core.py || true)
  if [ -n "$hits" ]; then
    bad "sdist contains credential-shaped strings outside the redaction fixtures:"
    echo "$hits" | head
  else
    ok "sdist has no credential-shaped strings (redaction fixtures excepted)"
  fi
  rm -rf "$tmpx" "$listing"
fi

# ---------------------------------------------------------------- install smoke test
step "Clean-install smoke test"
smoke=$(mktemp -d)
if python3 -m venv "$smoke/venv" >/dev/null 2>&1 \
   && "$smoke/venv/bin/pip" -q install dist/*.whl >/dev/null 2>&1; then
  got=$("$smoke/venv/bin/memd" --version 2>&1 || true)
  if [ "$got" = "memd $VERSION" ]; then
    ok "installed wheel reports '$got'"
  else
    bad "installed wheel reports '$got', expected 'memd $VERSION'"
  fi
  # A scaffolded project must never put memd's commits in the user's history.
  proj="$smoke/proj"; mkdir -p "$proj"
  git -C "$proj" init -q
  git -C "$proj" config user.email r@r; git -C "$proj" config user.name r
  echo x >"$proj/x.py"; git -C "$proj" add -A; git -C "$proj" commit -qm init
  HOME="$smoke/home" XDG_CONFIG_HOME="$smoke/home/.config" \
    XDG_STATE_HOME="$smoke/home/.local/state" \
    "$smoke/venv/bin/memd" init "$proj" >/dev/null 2>&1 || true
  subjects=$(git -C "$proj" log --format=%s | tr '\n' ' ')
  if [ "$(echo "$subjects" | xargs)" = "init" ]; then
    ok "memd init leaves the project's own git history untouched"
  else
    bad "memd wrote commits into the project repo: $subjects"
  fi
  if [ -d "$proj/.memory/.git" ]; then
    ok ".memory got its own git repo"
  else
    bad ".memory has no standalone repo — memory would leak upward"
  fi
else
  bad "could not build a clean venv install"
fi
rm -rf "$smoke"

# ---------------------------------------------------------------- credentials
step "Upload credentials"
if [ -n "${TWINE_PASSWORD:-}" ] || [ -f "$HOME/.pypirc" ]; then
  ok "PyPI credentials present (~/.pypirc or TWINE_PASSWORD)"
else
  warn "no ~/.pypirc or TWINE_PASSWORD; twine will prompt for a token"
fi

# ---------------------------------------------------------------- verdict
echo
if [ "$fail" -ne 0 ]; then
  printf '\033[31mDue diligence FAILED — nothing was pushed or uploaded.\033[0m\n'
  exit 1
fi
printf '\033[32mAll due-diligence gates passed for %s.\033[0m\n' "$VERSION"

if [ "$DO_RELEASE" != 1 ]; then
  echo "Dry run (no --release): nothing pushed, tagged, or uploaded."
  echo "When ready:  ./scripts/release.sh --release"
  exit 0
fi

# ================================================================ RELEASE STEPS
# Everything below is irreversible or public.
step "RELEASE STEPS (irreversible)"
echo "  branch : $BRANCH -> origin/$BRANCH"
echo "  tag    : $TAG"
echo "  upload : $(find dist -maxdepth 1 -type f -printf '%f ')"
confirm "Proceed with push, tag, and PyPI upload of $VERSION?"

step "Pushing $BRANCH"
git push -u origin "$BRANCH"
ok "pushed"

step "Tagging $TAG"
git tag -a "$TAG" -m "memd $VERSION"
git push origin "$TAG"
ok "tag pushed"

step "Uploading to PyPI"
confirm "Upload to PyPI now? A version cannot be replaced once uploaded."
python3 -m twine upload dist/*
ok "uploaded"

# ---------------------------------------------------------------- post-release
step "Post-release verification"
echo "  waiting for the index to serve $VERSION..."
verify=$(mktemp -d)
python3 -m venv "$verify/venv" >/dev/null 2>&1
for attempt in 1 2 3 4 5 6; do
  if "$verify/venv/bin/pip" -q install --no-cache-dir "memd==$VERSION" >/dev/null 2>&1; then
    got=$("$verify/venv/bin/memd" --version 2>&1 || true)
    if [ "$got" = "memd $VERSION" ]; then
      ok "PyPI serves $VERSION and it runs"
    else
      warn "PyPI install reports '$got'"
    fi
    break
  fi
  [ "$attempt" = 6 ] && warn "PyPI has not served $VERSION yet; re-check shortly"
  sleep 20
done
rm -rf "$verify"

cat <<EOF

Released $VERSION.

Still to do by hand:
  - GitHub release notes: paste the [$VERSION] section of CHANGELOG.md
  - Confirm CI is green on the pushed commit
  - Open a new '## [Unreleased]' section in CHANGELOG.md for the next cycle
EOF
