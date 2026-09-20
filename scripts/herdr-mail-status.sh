#!/usr/bin/env bash
# Herdr tab_bar_right: print "mail N" when any live seat has unread bus mail.
set -euo pipefail
export PEER_BUS_HERDR_SESSION="${PEER_BUS_HERDR_SESSION:-${AGENTS_HERDR_SESSION:-agents}}"
n="$(peer-bus mail --flock 2>/dev/null || echo 0)"
case "$n" in
  ''|*[!0-9]*) exit 0 ;;
esac
if [ "$n" -gt 0 ]; then
  printf 'mail %s\n' "$n"
fi
