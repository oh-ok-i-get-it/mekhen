#!/bin/sh
# Claude Code hook launcher for mekhen: finds a Python 3.11+ and runs the copy of mekhen bundled
# with this plugin. Hooks fire on every edit, so anything outside plan mode returns before Python
# even starts. It never fails in a way that interrupts Claude: with no suitable Python it does
# nothing.

input=$(cat)
case "$input" in
  *'"permission_mode":"plan"'* | *'"permission_mode": "plan"'*) ;;
  *) exit 0 ;;
esac

here=$(dirname "$0")
for py in python3 python py; do
  command -v "$py" >/dev/null 2>&1 || continue
  flag=
  [ "$py" = py ] && flag=-3
  # Skips too-old interpreters, and Windows' "python" Store stubs (they exit non-zero).
  "$py" $flag -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1 || continue
  printf '%s' "$input" | "$py" $flag "$here/hook.py" "$@"
  exit $?
done
exit 0
