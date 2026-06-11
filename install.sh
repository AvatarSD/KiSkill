#!/usr/bin/env bash
# KiSkill installer — wires this clone into Claude Code (or any agent that
# reads ~/.claude/skills) without copying anything. Idempotent: safe to
# re-run after a `git pull`.
#
#   ./install.sh            # symlink skills + kx into your home
#   ./install.sh --uninstall
#
# What it does:
#   * symlinks each skills/<name> into ~/.claude/skills/<name>
#   * symlinks bin/kx into ~/.local/bin/kx   (the location-independent CLI)
#   * verifies python3 + kicad-cli are visible (warns, never blocks)
#
# The engine (kicad_lib/) is pure Python stdlib — no pip install needed.
set -euo pipefail

REPO="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
SKILLS_DST="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
BIN_DST="${KX_BIN_DIR:-$HOME/.local/bin}"

link() {  # link SRC DST — replace only our own prior symlink
  local src="$1" dst="$2"
  if [ -e "$dst" ] && [ ! -L "$dst" ]; then
    echo "  ! $dst exists and is not a symlink — skipping (move it aside)"
    return
  fi
  ln -sfn "$src" "$dst"
  echo "  → $dst"
}

uninstall() {
  echo "Removing KiSkill symlinks…"
  for d in "$REPO"/skills/*/; do
    local name; name="$(basename "$d")"
    local t="$SKILLS_DST/$name"
    [ -L "$t" ] && [ "$(readlink -f "$t")" = "$(readlink -f "$d")" ] && { rm "$t"; echo "  ✗ $t"; }
  done
  local kx="$BIN_DST/kx"
  [ -L "$kx" ] && [ "$(readlink -f "$kx")" = "$REPO/bin/kx" ] && { rm "$kx"; echo "  ✗ $kx"; }
  echo "Done."
}

if [ "${1:-}" = "--uninstall" ]; then uninstall; exit 0; fi

echo "Installing KiSkill from: $REPO"
mkdir -p "$SKILLS_DST" "$BIN_DST"

echo "Skills → $SKILLS_DST"
for d in "$REPO"/skills/*/; do
  link "$d" "$SKILLS_DST/$(basename "$d")"
done

echo "CLI → $BIN_DST/kx"
link "$REPO/bin/kx" "$BIN_DST/kx"

echo
echo "Checks:"
command -v python3 >/dev/null && echo "  ✓ python3 $(python3 --version 2>&1 | awk '{print $2}')" \
  || echo "  ! python3 not found (required for the engine)"
case ":$PATH:" in *":$BIN_DST:"*) echo "  ✓ $BIN_DST on PATH" ;;
  *) echo "  ! $BIN_DST is NOT on PATH — add it to use 'kx' directly" ;; esac
command -v kicad-cli >/dev/null && echo "  ✓ kicad-cli present" \
  || echo "  · kicad-cli not on PATH (skills fall back to flatpak; install KiCad 9+ for ERC/render/fab)"

echo
echo "Installed. Verify:  kx root   (prints this repo)   |   kx   (lists commands)"
echo "Then ask your agent: \"use the kicad-project skill to open <path>.kicad_pro\""
