#!/usr/bin/env bash
#
# Auto-configure PrintMCP for a local MCP client on macOS or Linux.
#
# Detects installed MCP clients (Claude Code, Claude Desktop, Cursor, Windsurf,
# opencode), lets you pick one, and writes the PrintMCP server into that client's
# config so it launches automatically.
#
# By default the client runs the published package (`uvx printmcp`). Pass
# --directory <path> to point at a local PrintMCP checkout instead (contributors).
#
# IMPORTANT: GUI clients keep their config in memory and rewrite it on exit, so
# edits made while the client is running get clobbered. If the chosen client is
# running, this script stops and asks you to close it and run again (override
# with --force).
#
# Usage:
#   ./scripts/setup-mcp.sh                    # interactive (uses uvx printmcp)
#   ./scripts/setup-mcp.sh --list             # list detected clients, then exit
#   ./scripts/setup-mcp.sh --client cursor    # configure a specific client
#   ./scripts/setup-mcp.sh --directory /path/to/PrintMCP   # use a local clone
#   ./scripts/setup-mcp.sh --force            # apply even if the client is running
#
# JSON editing is done with Python (always present for a Python project), so no
# `jq` dependency. No secrets are written to client configs — PrintMCP reads
# those from the project's .env at startup.

set -euo pipefail

SERVER_NAME="printmcp"
PROJECT_DIR=""   # set => run `uv run --directory <path> printmcp` (local checkout)

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
    --directory|--from-path|--project) shift; PROJECT_DIR="${1:-}" ;;
    --directory=*|--from-path=*|--project=*) PROJECT_DIR="${1#*=}" ;;
    -h|--help)
      sed -n '3,30p' "$0" | sed 's/^# \{0,1\}//'
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

# The command the client will run. Default: published package via uvx.
# --directory <path> switches to a local checkout instead.
if [ -n "$PROJECT_DIR" ]; then
  # Resolve to an absolute path; if cd fails, leave PROJECT_DIR empty (the
  # next check catches it). Written as if/else (not cd && pwd || true) — SC2015.
  if _resolved="$(cd "$PROJECT_DIR" 2>/dev/null && pwd)"; then
    PROJECT_DIR="$_resolved"
  else
    PROJECT_DIR=""
  fi
  if [ -z "$PROJECT_DIR" ] || [ ! -f "$PROJECT_DIR/pyproject.toml" ]; then
    err "--directory must point at a PrintMCP checkout (no pyproject.toml at '$PROJECT_DIR')."
    exit 1
  fi
  # Quote the dir so paths with spaces survive shlex.split in the writers.
  LAUNCH_DESC="uv run --directory \"$PROJECT_DIR\" printmcp"
else
  # Sanity: the package must at least resolve via uvx before we wire a client to it.
  if ! "$UV_BIN" tool run --from printmcp printmcp --version >/dev/null 2>&1 \
     && ! uvx --from printmcp printmcp --version >/dev/null 2>&1; then
    warn "Could not resolve 'printmcp' from PyPI via uvx (offline or not published yet?). Continuing."
  fi
  LAUNCH_DESC="uvx printmcp"
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
  CFG="$1" LAUNCH="$LAUNCH_DESC" NAME="$SERVER_NAME" "$PY_BIN" - <<'PY'
import json, os, shlex, sys
cfg, launch, name = os.environ["CFG"], os.environ["LAUNCH"], os.environ["NAME"]
parts = shlex.split(launch)
data = {}
if os.path.exists(cfg) and os.path.getsize(cfg):
    with open(cfg, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception:
            sys.stderr.write("EXISTING_UNPARSEABLE\n"); sys.exit(3)
data.setdefault("mcpServers", {})
data["mcpServers"][name] = {
    "command": parts[0],
    "args": parts[1:],
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
  CFG="$1" LAUNCH="$LAUNCH_DESC" NAME="$SERVER_NAME" NEW="$is_new" "$PY_BIN" - <<'PY'
import json, os, shlex, sys
cfg, launch, name = os.environ["CFG"], os.environ["LAUNCH"], os.environ["NAME"]
is_new = os.environ["NEW"] == "1"
parts = shlex.split(launch)
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
    "command": parts,
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
  # LAUNCH_DESC may contain a quoted path (spaces); split it into argv safely
  # (no eval: the user supplies the path via --directory).
  "$PY_BIN" - "$LAUNCH_DESC" "$SERVER_NAME" "$CLAUDE_CLI_BIN" <<'PY'
import shlex, subprocess, sys
launch, name, claude_bin = sys.argv[1], sys.argv[2], sys.argv[3]
parts = shlex.split(launch)
result = subprocess.run(
    [claude_bin, "mcp", "add", "--scope", "user", "--transport", "stdio", name, "--"] + parts
)
sys.exit(result.returncode)
PY
}

print_manual() {  # $1=format $2=path
  warn "Add this to $2 manually:"
  FMT="$1" LAUNCH="$LAUNCH_DESC" NAME="$SERVER_NAME" "$PY_BIN" - <<'PY'
import json, os, shlex
fmt, launch, name = os.environ["FMT"], os.environ["LAUNCH"], os.environ["NAME"]
parts = shlex.split(launch)
if fmt == "opencode":
    obj = {"mcp": {name: {"type": "local", "command": parts, "enabled": True}}}
else:
    obj = {"mcpServers": {name: {"command": parts[0], "args": parts[1:], "env": {}}}}
print(json.dumps(obj, indent=2))
PY
}

# ---- main ----------------------------------------------------------------- #
step "PrintMCP client setup"
info "      uv:      $UV_BIN"
info "      launch:  $LAUNCH_DESC"
if [ -n "$PROJECT_DIR" ]; then
  info "      (using local checkout: $PROJECT_DIR)"
else
  info "      (using the published package from PyPI)"
fi
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
if [ -n "$PROJECT_DIR" ]; then
  info "  1. Make sure your .env is set up in:"
  info "       $PROJECT_DIR"
  info "     (copy .env.example to .env and fill in THINGIVERSE_TOKEN, and the"
  info "      OCTOPRINT_* values if you'll print). See docs/getting-started.md."
else
  info "  1. PrintMCP reads secrets from a .env in the working directory where the"
  info "     client launches it (or from environment variables). Create one with"
  info "     THINGIVERSE_TOKEN and, for printing, OCTOPRINT_URL / OCTOPRINT_API_KEY."
  info "     See https://github.com/SourceBox-LLC/PrintMCP#getting-started."
fi
if [ "$fmt" = "claude-cli" ]; then
  info "  2. Start a new 'claude' session - PrintMCP's tools will be available."
else
  info "  2. Start $(client_name "$TARGET"). PrintMCP's tools will load on launch."
fi
info ""
exit 0
