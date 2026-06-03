#!/usr/bin/env bash
#
# Auto-configure PrintMCP for a local MCP client on macOS or Linux.
#
# Detects installed MCP clients (Claude Code, Claude Desktop, Cursor, Windsurf,
# opencode), lets you pick one, and writes the PrintMCP server into that client's
# config so it launches `uv run --directory <project> printmcp` automatically.
#
# IMPORTANT: GUI clients keep their config in memory and rewrite it on exit, so
# edits made while the client is running get clobbered. If the chosen client is
# running, this script stops and asks you to close it and run again (override
# with --force).
#
# Usage:
#   ./scripts/setup-mcp.sh                 # interactive
#   ./scripts/setup-mcp.sh --list          # list detected clients, then exit
#   ./scripts/setup-mcp.sh --client cursor # configure a specific client
#   ./scripts/setup-mcp.sh --force         # apply even if the client is running
#
# JSON editing is done with Python (always present for a Python project), so no
# `jq` dependency. No secrets are written to client configs — PrintMCP reads
# those from the project's .env at startup.

set -euo pipefail

SERVER_NAME="printmcp"

# ---- output helpers ------------------------------------------------------- #
if [ -t 1 ]; then
  C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
  C_RED=$'\033[31m'; C_DIM=$'\033[2m'; C_RESET=$'\033[0m'
else
  C_CYAN=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_DIM=""; C_RESET=""
fi
info()  { printf '%s\n' "$*"; }
step()  { printf '%s==> %s%s\n' "$C_CYAN" "$*" "$C_RESET"; }
ok()    { printf '%s[ OK ] %s%s\n' "$C_GREEN" "$*" "$C_RESET"; }
warn()  { printf '%s[WARN] %s%s\n' "$C_YELLOW" "$*" "$C_RESET"; }
err()   { printf '%s[FAIL] %s%s\n' "$C_RED" "$*" "$C_RESET" >&2; }

# ---- args ----------------------------------------------------------------- #
CLIENT=""; DO_LIST=0; FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --list) DO_LIST=1 ;;
    --force) FORCE=1 ;;
    --client) shift; CLIENT="${1:-}" ;;
    --client=*) CLIENT="${1#*=}" ;;
    -h|--help)
      sed -n '3,28p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) err "Unknown argument: $1"; exit 1 ;;
  esac
  shift
done

# ---- preflight ------------------------------------------------------------ #
UV_BIN="$(command -v uv || true)"
if [ -z "$UV_BIN" ]; then
  err "Could not find 'uv' on PATH. Install it from https://docs.astral.sh/uv/ and re-run."
  exit 1
fi

PY_BIN="$(command -v python3 || command -v python || true)"
if [ -z "$PY_BIN" ]; then
  err "Could not find python3/python on PATH (needed to edit JSON configs)."
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [ ! -f "$PROJECT_ROOT/pyproject.toml" ]; then
  err "Could not find pyproject.toml at $PROJECT_ROOT. Run this from inside the PrintMCP repo."
  exit 1
fi
if ! grep -q 'name = "printmcp"' "$PROJECT_ROOT/pyproject.toml" 2>/dev/null; then
  warn "pyproject.toml at $PROJECT_ROOT doesn't look like PrintMCP; continuing anyway."
fi

# ---- platform config paths ------------------------------------------------ #
OS="$(uname -s)"
if [ "$OS" = "Darwin" ]; then
  CLAUDE_DESKTOP_CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
  WINDSURF_CFG="$HOME/.codeium/windsurf/mcp_config.json"
else
  CLAUDE_DESKTOP_CFG="${XDG_CONFIG_HOME:-$HOME/.config}/Claude/claude_desktop_config.json"
  WINDSURF_CFG="$HOME/.codeium/windsurf/mcp_config.json"
fi
CURSOR_CFG="$HOME/.cursor/mcp.json"
CLAUDE_CODE_CFG="$HOME/.claude.json"
OPENCODE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
if [ -f "$OPENCODE_DIR/opencode.jsonc" ]; then
  OPENCODE_CFG="$OPENCODE_DIR/opencode.jsonc"
else
  OPENCODE_CFG="$OPENCODE_DIR/opencode.json"
fi

# Client table. Each row: id|name|format|config-path|detected(0/1)|procnames
CLAUDE_CLI_BIN="$(command -v claude || true)"
detected_claude_code=0
{ [ -n "$CLAUDE_CLI_BIN" ] || [ -f "$CLAUDE_CODE_CFG" ]; } && detected_claude_code=1
detected_claude_desktop=0; [ -d "$(dirname "$CLAUDE_DESKTOP_CFG")" ] && detected_claude_desktop=1
detected_cursor=0
{ [ -d "$HOME/.cursor" ] || command -v cursor >/dev/null 2>&1; } && detected_cursor=1
detected_windsurf=0; [ -d "$HOME/.codeium/windsurf" ] && detected_windsurf=1
detected_opencode=0
{ [ -d "$OPENCODE_DIR" ] || command -v opencode >/dev/null 2>&1; } && detected_opencode=1

client_ids=(claude-code claude-desktop cursor windsurf opencode)
client_name()    { case "$1" in
  claude-code) echo "Claude Code (CLI)";; claude-desktop) echo "Claude Desktop";;
  cursor) echo "Cursor";; windsurf) echo "Windsurf";; opencode) echo "opencode";; esac; }
client_format()  { case "$1" in
  claude-code) echo "claude-cli";; opencode) echo "opencode";; *) echo "mcpServers";; esac; }
client_cfg()     { case "$1" in
  claude-code) echo "$CLAUDE_CODE_CFG";; claude-desktop) echo "$CLAUDE_DESKTOP_CFG";;
  cursor) echo "$CURSOR_CFG";; windsurf) echo "$WINDSURF_CFG";; opencode) echo "$OPENCODE_CFG";; esac; }
client_detected(){ case "$1" in
  claude-code) echo "$detected_claude_code";; claude-desktop) echo "$detected_claude_desktop";;
  cursor) echo "$detected_cursor";; windsurf) echo "$detected_windsurf";;
  opencode) echo "$detected_opencode";; esac; }
client_procs()   { case "$1" in
  claude-code) echo "claude";; claude-desktop) echo "Claude";;
  cursor) echo "Cursor cursor";; windsurf) echo "Windsurf windsurf";; opencode) echo "opencode";; esac; }

is_running() {
  local names; names="$(client_procs "$1")"
  local n
  for n in $names; do
    if command -v pgrep >/dev/null 2>&1; then
      # macOS / most Linux: exact name, or a path ending in /<name>.
      if pgrep -x "$n" >/dev/null 2>&1 || pgrep -f "[/ ]$n( |$)" >/dev/null 2>&1; then
        return 0
      fi
    else
      # Fallback when pgrep is unavailable: scan `ps` output. (pgrep is the
      # preferred path above; this branch only runs when it's missing, so the
      # SC2009 "use pgrep" suggestion doesn't apply here.)
      # shellcheck disable=SC2009
      if ps -A -o comm= 2>/dev/null | grep -qx "$n" \
        || ps -A 2>/dev/null | grep -E "[/ ]$n( |$)" | grep -qv grep; then
        return 0
      fi
    fi
  done
  return 1
}

# ---- JSON writers (via embedded Python) ----------------------------------- #
backup_if_exists() {
  if [ -f "$1" ]; then
    local b
    b="$1.printmcp-backup-$(date +%Y%m%d-%H%M%S)"
    cp "$1" "$b"
    info "      backed up existing config -> $b"
  fi
}

apply_mcpservers() {  # $1=config path
  backup_if_exists "$1"
  CFG="$1" UV="$UV_BIN" ROOT="$PROJECT_ROOT" NAME="$SERVER_NAME" "$PY_BIN" - <<'PY'
import json, os, sys
cfg, uv, root, name = os.environ["CFG"], os.environ["UV"], os.environ["ROOT"], os.environ["NAME"]
data = {}
if os.path.exists(cfg) and os.path.getsize(cfg):
    with open(cfg, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception:
            sys.stderr.write("EXISTING_UNPARSEABLE\n"); sys.exit(3)
data.setdefault("mcpServers", {})
data["mcpServers"][name] = {
    "command": uv,
    "args": ["run", "--directory", root, name],
    "env": {},
}
os.makedirs(os.path.dirname(cfg) or ".", exist_ok=True)
with open(cfg, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
}

apply_opencode() {  # $1=config path
  local is_new=1; [ -f "$1" ] && is_new=0
  backup_if_exists "$1"
  CFG="$1" UV="$UV_BIN" ROOT="$PROJECT_ROOT" NAME="$SERVER_NAME" NEW="$is_new" "$PY_BIN" - <<'PY'
import json, os, sys
cfg, uv, root, name = os.environ["CFG"], os.environ["UV"], os.environ["ROOT"], os.environ["NAME"]
is_new = os.environ["NEW"] == "1"
data = {}
if os.path.exists(cfg) and os.path.getsize(cfg):
    with open(cfg, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception:
            sys.stderr.write("EXISTING_UNPARSEABLE\n"); sys.exit(3)
if is_new:
    data.setdefault("$schema", "https://opencode.ai/config.json")
data.setdefault("mcp", {})
data["mcp"][name] = {
    "type": "local",
    "command": [uv, "run", "--directory", root, name],
    "enabled": True,
}
os.makedirs(os.path.dirname(cfg) or ".", exist_ok=True)
with open(cfg, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
}

apply_claude_cli() {
  if [ -z "$CLAUDE_CLI_BIN" ]; then
    err "The 'claude' CLI isn't on PATH, so PrintMCP can't be added automatically."
    print_manual mcpServers "$CLAUDE_CODE_CFG"
    return 1
  fi
  "$CLAUDE_CLI_BIN" mcp remove "$SERVER_NAME" --scope user >/dev/null 2>&1 || true
  "$CLAUDE_CLI_BIN" mcp add --scope user --transport stdio "$SERVER_NAME" \
    -- "$UV_BIN" run --directory "$PROJECT_ROOT" "$SERVER_NAME"
}

print_manual() {  # $1=format $2=path
  warn "Add this to $2 manually:"
  if [ "$1" = "opencode" ]; then
    cat <<EOF
{
  "mcp": {
    "$SERVER_NAME": {
      "type": "local",
      "command": ["$UV_BIN", "run", "--directory", "$PROJECT_ROOT", "$SERVER_NAME"],
      "enabled": true
    }
  }
}
EOF
  else
    cat <<EOF
{
  "mcpServers": {
    "$SERVER_NAME": {
      "command": "$UV_BIN",
      "args": ["run", "--directory", "$PROJECT_ROOT", "$SERVER_NAME"],
      "env": {}
    }
  }
}
EOF
  fi
}

# ---- main ----------------------------------------------------------------- #
step "PrintMCP client setup"
info "      uv:      $UV_BIN"
info "      project: $PROJECT_ROOT"
info ""

if [ "$DO_LIST" -eq 1 ]; then
  step "Detected MCP clients"
  for id in "${client_ids[@]}"; do
    if [ "$(client_detected "$id")" = "1" ]; then
      printf '  %s[detected]%s  %-18s %s\n' "$C_GREEN" "$C_RESET" "$(client_name "$id")" "$(client_cfg "$id")"
    else
      printf '  %s[not found] %-18s %s%s\n' "$C_DIM" "$(client_name "$id")" "$(client_cfg "$id")" "$C_RESET"
    fi
  done
  exit 0
fi

# choose target
TARGET=""
if [ -n "$CLIENT" ]; then
  for id in "${client_ids[@]}"; do [ "$id" = "$CLIENT" ] && TARGET="$id"; done
  if [ -z "$TARGET" ]; then err "Unknown client id: $CLIENT"; exit 1; fi
  if [ "$(client_detected "$TARGET")" != "1" ]; then
    warn "$(client_name "$TARGET") wasn't detected, but proceeding because you named it."
  fi
else
  detected=()
  for id in "${client_ids[@]}"; do [ "$(client_detected "$id")" = "1" ] && detected+=("$id"); done
  if [ "${#detected[@]}" -eq 0 ]; then
    err "No supported MCP clients were detected."
    info "Checked: Claude Code, Claude Desktop, Cursor, Windsurf, opencode."
    info "Install one, or re-run with --client <id>."
    exit 1
  fi
  step "Which client should I configure?"
  i=1
  for id in "${detected[@]}"; do
    printf '  [%d] %s\n' "$i" "$(client_name "$id")"
    printf '      %s%s%s\n' "$C_DIM" "$(client_cfg "$id")" "$C_RESET"
    i=$((i + 1))
  done
  info ""
  printf 'Enter a number (1-%d), or q to quit: ' "${#detected[@]}"
  read -r choice
  case "$choice" in
    q|Q) info "Cancelled."; exit 0 ;;
    *[!0-9]*|"") err "Invalid selection."; exit 1 ;;
  esac
  if [ "$choice" -lt 1 ] || [ "$choice" -gt "${#detected[@]}" ]; then
    err "Invalid selection."; exit 1
  fi
  TARGET="${detected[$((choice - 1))]}"
fi

info ""
step "Target: $(client_name "$TARGET")"

# the crucial guard
if is_running "$TARGET"; then
  if [ "$FORCE" -eq 1 ]; then
    warn "$(client_name "$TARGET") appears to be running, but --force was given. Continuing."
  else
    err "$(client_name "$TARGET") is currently running."
    info ""
    info "  MCP clients keep their config in memory and rewrite it when they close,"
    info "  which would erase the changes this script makes. Please:"
    info ""
    info "    1. Fully quit $(client_name "$TARGET")."
    info "    2. Run this script again."
    info ""
    info "  (Advanced: re-run with --force to configure anyway.)"
    exit 2
  fi
fi

# apply
fmt="$(client_format "$TARGET")"
cfg="$(client_cfg "$TARGET")"
case "$fmt" in
  claude-cli)
    apply_claude_cli ;;
  opencode)
    if ! apply_opencode "$cfg"; then
      err "Existing config at $cfg isn't valid JSON; not overwriting it."
      print_manual opencode "$cfg"; exit 1
    fi ;;
  *)
    if ! apply_mcpservers "$cfg"; then
      err "Existing config at $cfg isn't valid JSON; not overwriting it."
      print_manual mcpServers "$cfg"; exit 1
    fi ;;
esac

ok "PrintMCP configured for $(client_name "$TARGET")."
info ""
step "Next steps"
info "  1. Make sure your .env is set up in:"
info "       $PROJECT_ROOT"
info "     (copy .env.example to .env and fill in THINGIVERSE_TOKEN, and the"
info "      OCTOPRINT_* values if you'll print). See docs/getting-started.md."
if [ "$fmt" = "claude-cli" ]; then
  info "  2. Start a new 'claude' session - PrintMCP's tools will be available."
else
  info "  2. Start $(client_name "$TARGET"). PrintMCP's tools will load on launch."
fi
info ""
exit 0
