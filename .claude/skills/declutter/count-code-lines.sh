#!/bin/sh
# Count code lines in the repository: tracked files only, minus blank lines and lines
# that resemble comments. Comment syntax is picked per file type, so a "#" line in a
# shell script counts as a comment while a "#" inside .json does not exist at all.
#
#   .claude/skills/declutter/count-code-lines.sh            total only
#   .claude/skills/declutter/count-code-lines.sh --by-file  per-file counts, first the
#                                                           largest, then the total
set -e
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"

# Python-style docstrings are prose, not code, so they are stripped for .py files before
# the line filter runs; every other type is filtered by its line-comment marker alone.
count_file() {
  case "$1" in
    *.py)
      python3 - "$1" <<'PY'
import io, sys, tokenize
path = sys.argv[1]
with open(path, "rb") as handle:
    source = handle.read()
skip = set()
try:
    tokens = list(tokenize.tokenize(io.BytesIO(source).readline))
except (tokenize.TokenError, IndentationError, SyntaxError):
    tokens = []
prev = tokenize.INDENT
for token in tokens:
    # A string that stands alone as its own statement is a docstring, not a value.
    if token.type == tokenize.STRING and prev in (
        tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE, tokenize.NL, tokenize.ENCODING
    ):
        skip.update(range(token.start[0], token.end[0] + 1))
    if token.type not in (tokenize.NL, tokenize.COMMENT):
        prev = token.type
lines = source.decode("utf-8", "replace").splitlines()
print(sum(
    1 for n, line in enumerate(lines, 1)
    if n not in skip and line.strip() and not line.lstrip().startswith("#")
))
PY
      ;;
    *.sh|*.container|*.pod|*.volume|*.service|*.timer|*.env|*.yml|*.yaml|*.toml|Dockerfile|*/Dockerfile)
      grep -cvE '^\s*(#|$)' "$1" || true ;;
    *.sql)
      grep -cvE '^\s*(--|$)' "$1" || true ;;
    *.ini)
      grep -cvE '^\s*([;#]|$)' "$1" || true ;;
    *.conf|*.template)
      grep -cvE '^\s*(#|$)' "$1" || true ;;
    *.html)
      grep -cvE '^\s*(\{#|<!--|$)' "$1" || true ;;
    *.json)
      grep -cvE '^\s*$' "$1" || true ;;
    *) return ;;   # prose, lockfiles, licences: not code
  esac
}

total=0
for f in $(git ls-files); do
  [ -f "$f" ] || continue
  case "$f" in
    # Generated rather than authored, and the agent tooling under .claude/ is not the
    # product -- counting the counter would move the total whenever the skill is edited.
    */uv.lock|uv.lock|*/migrations/*|.claude/*) continue ;;
  esac
  n="$(count_file "$f")"
  [ -n "$n" ] || continue
  total=$((total + n))
  [ "${1:-}" = "--by-file" ] && printf '%6d  %s\n' "$n" "$f"
done | { [ "${1:-}" = "--by-file" ] && sort -rn || cat; }

# The loop above runs in a subshell when piped, so recompute the total for printing.
total=0
for f in $(git ls-files); do
  [ -f "$f" ] || continue
  case "$f" in
    */uv.lock|uv.lock|*/migrations/*|.claude/*) continue ;;
  esac
  n="$(count_file "$f")"
  [ -n "$n" ] || continue
  total=$((total + n))
done
echo "TOTAL CODE LINES: $total"
