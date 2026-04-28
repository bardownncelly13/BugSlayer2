import argparse
import os
import re
import shlex
import subprocess
import textwrap
from pathlib import Path

from dotenv import load_dotenv
from google.api_core.exceptions import NotFound
from google.cloud import compute_v1

load_dotenv()

PROJECT_ID = os.environ["PROJECT_ID"]
ZONE = os.environ.get("ZONE", "us-central1-a")
DEFAULT_INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "nexus-api-lab")

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SSH_USER = os.environ.get("SSH_USER", "ubuntu")
GOALS_PATH = os.environ.get("GOALS_PATH", "GOALS.md")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL") or os.environ.get("WEBHOOK_URL", "")
SUB_PATTERN = r'rf"^[ \\t]*---BEGIN {{re.escape(filename)}}---[ \\t]*\\r?\\n(.*?)[ \\t]*---END {{re.escape(filename)}}---[ \\t]*$"'
TARGET_DIR = "/opt/project"
ORCH_DIR = "/opt/orchestrator"
CLAUDE_TEMPLATE_PATH = "claude.json.template"


def wait_for_zone_op(project: str, zone: str, op_name: str):
    zone_ops = compute_v1.ZoneOperationsClient()
    while True:
        result = zone_ops.get(project=project, zone=zone, operation=op_name)
        if result.status == compute_v1.Operation.Status.DONE:
            if result.error:
                raise RuntimeError(result.error)
            return result


def delete_instance_if_exists(instance_client: compute_v1.InstancesClient, name: str):
    try:
        instance_client.get(project=PROJECT_ID, zone=ZONE, instance=name)
    except NotFound:
        return
    print(f"[*] Deleting existing instance {name} ...")
    op = instance_client.delete(project=PROJECT_ID, zone=ZONE, instance=name)
    wait_for_zone_op(PROJECT_ID, ZONE, op.name)


def load_goals_md() -> str:
    with open(GOALS_PATH, "r", encoding="utf-8") as handle:
        return handle.read().rstrip() + "\n"


def load_claude_template() -> str:
    with open(CLAUDE_TEMPLATE_PATH, "r", encoding="utf-8") as handle:
        return handle.read().rstrip() + "\n"


def shell_quote(value: str) -> str:
    return shlex.quote(value)


def wrap_step(name: str, body: str) -> str:
    body = textwrap.dedent(body).strip()
    return textwrap.dedent(
        f"""
        echo "=== STEP: {name} ==="
        {body}
        echo "=== DONE: {name} ==="
        """
    ).strip()


def build_post_upload_script(discord_webhook_url: str) -> str:
    webhook_value = shell_quote(discord_webhook_url)
    return textwrap.dedent(
        f"""        #!/usr/bin/env bash
        set -euo pipefail

        OUT_DIR="{ORCH_DIR}"
        WEBHOOK_URL={webhook_value}
        POST_LOG="$OUT_DIR/post_upload.log"
        ZIP_PATH="/tmp/orchestrator_outputs_manual.zip"

        exec >> "$POST_LOG" 2>&1
        echo "[post-upload $(date -Is)] starting"

        cd "$OUT_DIR"

        for f in ENTRYPOINTS.md VULN_REPORT.md POC.md PATCH.md; do
          if [ ! -s "$f" ]; then
            echo "[post-upload $(date -Is)] missing $f; skipping upload"
            exit 0
          fi
        done

        if [ -z "$WEBHOOK_URL" ]; then
          echo "[post-upload $(date -Is)] webhook not configured; skipping upload"
          exit 0
        fi

        rm -f "$ZIP_PATH"
        zip -r "$ZIP_PATH"           ENTRYPOINTS.md VULN_REPORT.md POC.md PATCH.md           CLAUDE_RAW.txt GOALS.md GITNEXUS_STATUS.txt           NEXUS_LIST.txt NEXUS_ENTRYPOINTS.md NEXUS_HOTSPOTS.md

        curl -F 'payload_json={{"content":"analysis complete from vm"}}'              -F "file=@$ZIP_PATH"              "$WEBHOOK_URL"

        echo "[post-upload $(date -Is)] upload complete"
        """
    )


def build_orchestrator_script(discord_webhook_url: str) -> str:
    webhook_value = shell_quote(discord_webhook_url)
    return textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        set -euo pipefail

        REPO_DIR="{TARGET_DIR}"
        OUT_DIR="{ORCH_DIR}"
        RAW="$OUT_DIR/CLAUDE_RAW.txt"
        RUN_LOG="$OUT_DIR/boot.log"
        GITNEXUS_LIST="$OUT_DIR/NEXUS_LIST.txt"
        GITNEXUS_ENTRYPOINTS="$OUT_DIR/NEXUS_ENTRYPOINTS.md"
        GITNEXUS_HOTSPOTS="$OUT_DIR/NEXUS_HOTSPOTS.md"
        GITNEXUS_STATUS="$OUT_DIR/GITNEXUS_STATUS.txt"
        READY_FILE="$OUT_DIR/CLAUDE_READY"
        FINAL_ZIP="$OUT_DIR/final_outputs.zip"
        WEBHOOK_LOG="$OUT_DIR/webhook.log"
        EXTRACT_LOG="$OUT_DIR/extract.log"

        log() {{
          printf '%s %s\\n' "[$(date -Is)]" "$*" | tee -a "$RUN_LOG"
        }}

        send_webhook() {{
          local message="$1"
          local zip_path="$2"
          WEBHOOK_URL={webhook_value}
          if [ -z "$WEBHOOK_URL" ]; then
            return
          fi
          if [ -n "$zip_path" ] && [ -f "$zip_path" ]; then
            curl -fsSL "$WEBHOOK_URL" -F "payload_json={{\"content\":\"$message\"}}" -F "file=@$zip_path" >> "$WEBHOOK_LOG" 2>&1 || true
          else
            curl -fsSL "$WEBHOOK_URL" -F "payload_json={{\"content\":\"$message\"}}" >> "$WEBHOOK_LOG" 2>&1 || true
          fi
        }}

        package_outputs() {{
          local zip_path="$1"
          cd "$OUT_DIR"
          zip -q -r "$zip_path" \
            ENTRYPOINTS.md VULN_REPORT.md POC.md PATCH.md \
            CLAUDE_RAW.txt GOALS.md boot.log GITNEXUS_STATUS.txt \
            NEXUS_LIST.txt NEXUS_ENTRYPOINTS.md NEXUS_HOTSPOTS.md \
            extract.log webhook.log orchestrator-pane.log 2>/dev/null || true
        }}

        cd "$REPO_DIR"

        echo "Running GitNexus analysis..."
        gitnexus analyze .
        gitnexus list | tee "$GITNEXUS_LIST"
        gitnexus query "list the main runtime entrypoints in this repo with file paths" | tee "$GITNEXUS_ENTRYPOINTS"
        gitnexus query "identify the most connected functions, files, or hotspots in this repo" | tee "$GITNEXUS_HOTSPOTS"

        if grep -q "project" "$GITNEXUS_LIST" && [ -s "$GITNEXUS_ENTRYPOINTS" ] && [ -s "$GITNEXUS_HOTSPOTS" ]; then
          echo "gitnexus-ready" | tee "$GITNEXUS_STATUS"
          touch "$READY_FILE"
        else
          echo "gitnexus-not-ready" | tee "$GITNEXUS_STATUS"
          FINAL_ZIP="$OUT_DIR/final_outputs.zip"
          package_outputs "$FINAL_ZIP"
          send_webhook "GitNexus verification failed on $(hostname). Claude run was skipped." "$FINAL_ZIP"
          exit 1
        fi

        script -q -e -c 'claude --dangerously-skip-permissions "Follow /opt/orchestrator/GOALS.md exactly.

        Before doing anything else, read and use these GitNexus-generated files as your primary navigation map:
        - /opt/orchestrator/NEXUS_LIST.txt
        - /opt/orchestrator/NEXUS_ENTRYPOINTS.md
        - /opt/orchestrator/NEXUS_HOTSPOTS.md

        If those files are missing, empty, or indicate GitNexus is unavailable, stop immediately and say so.

        Write your final outputs directly to these exact files under /opt/orchestrator:
        - /opt/orchestrator/ENTRYPOINTS.md
        - /opt/orchestrator/VULN_REPORT.md
        - /opt/orchestrator/POC.md
        - /opt/orchestrator/PATCH.md

        After writing all four files, print a short completion message only."' "$RAW"

        missing_files=()
        for output in ENTRYPOINTS.md VULN_REPORT.md POC.md PATCH.md; do
          if [ ! -s "$OUT_DIR/$output" ]; then
            missing_files+=("$output")
          fi
        done

        if [ "${{#missing_files[@]}}" -eq 0 ]; then
          extraction_ok=1
        else
          printf 'Missing output files: %s\n' "${{missing_files[*]}}" > "$EXTRACT_LOG"
          extraction_ok=0
        fi

        FINAL_ZIP="$OUT_DIR/final_outputs.zip"
        package_outputs "$FINAL_ZIP"
        if [ "$extraction_ok" -eq 1 ]; then
          send_webhook "Analysis complete on $(hostname). Outputs attached." "$FINAL_ZIP"
        else
          send_webhook "Analysis completed on $(hostname), but output extraction failed. Logs attached." "$FINAL_ZIP"
          exit 1
        fi
        """
    )


def startup_preamble(repo_url: str, ssh_user: str, runtime_minutes: int) -> str:
    return textwrap.dedent(
        f"""\
        #!/bin/bash
        set -euxo pipefail
        export DEBIAN_FRONTEND=noninteractive

        exec > >(tee -a /var/log/startup-script.log | logger -t startup-script -s 2>/dev/console) 2>&1

        REPO_URL={shell_quote(repo_url)}
        SSH_USER={shell_quote(ssh_user)}
        RUNTIME_MINUTES={runtime_minutes}
        TARGET={shell_quote(TARGET_DIR)}
        ORCH_DIR={shell_quote(ORCH_DIR)}

        echo "=== startup-script begin: $(date -Is) ==="
        echo "Repo: $REPO_URL"
        echo "SSH_USER: $SSH_USER"
        echo "RUNTIME_MINUTES: $RUNTIME_MINUTES"

        retry() {{
          local attempts="$1"
          shift
          local try_num=1
          until "$@"; do
            if [ "$try_num" -ge "$attempts" ]; then
              return 1
            fi
            try_num=$((try_num + 1))
            sleep 5
          done
        }}

        user_home() {{
          getent passwd "$1" | cut -d: -f6
        }}
        """
    ).strip()


def step_install_base_packages() -> str:
    return wrap_step(
        "install-base-packages",
        """
        apt-get update
        apt-get install -y --no-install-recommends \
          ca-certificates curl gnupg git python3-pip tmux build-essential zip
        """,
    )


def step_install_node() -> str:
    return wrap_step(
        "install-node",
        """
        install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
        echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list
        apt-get update
        apt-get install -y nodejs
        corepack enable || true
        node -v
        npm -v
        """,
    )


def step_install_claude() -> str:
    api_key_value = shell_quote(ANTHROPIC_API_KEY)
    return wrap_step(
        "install-claude",
        f"""
        retry 3 npm install -g @anthropic-ai/claude-code
        command -v claude
        claude --version || true
        id -u ubuntu >/dev/null 2>&1 || useradd -m -s /bin/bash ubuntu
        if ! grep -qxF "export ANTHROPIC_API_KEY={api_key_value}" /home/ubuntu/.bashrc 2>/dev/null; then
          echo "export ANTHROPIC_API_KEY={api_key_value}" >> /home/ubuntu/.bashrc
        fi
        if id -u "$SSH_USER" >/dev/null 2>&1; then
          USER_HOME="$(user_home "$SSH_USER")"
          if [ -n "$USER_HOME" ]; then
            touch "$USER_HOME/.bashrc"
            if ! grep -qxF "export ANTHROPIC_API_KEY={api_key_value}" "$USER_HOME/.bashrc" 2>/dev/null; then
              echo "export ANTHROPIC_API_KEY={api_key_value}" >> "$USER_HOME/.bashrc"
            fi
            chown "$SSH_USER:$SSH_USER" "$USER_HOME/.bashrc"
          fi
        fi
        if grep -q '^ANTHROPIC_API_KEY=' /etc/environment 2>/dev/null; then
          sed -i "s|^ANTHROPIC_API_KEY=.*$|ANTHROPIC_API_KEY={api_key_value}|" /etc/environment
        else
          echo "ANTHROPIC_API_KEY={api_key_value}" >> /etc/environment
        fi
        cat > /etc/profile.d/anthropic_api_key.sh <<'EOF_KEY'
export ANTHROPIC_API_KEY={api_key_value}
EOF_KEY
        chmod 0644 /etc/profile.d/anthropic_api_key.sh
        chown ubuntu:ubuntu /home/ubuntu/.bashrc
        """,
    )


def step_install_gitnexus() -> str:
    return wrap_step(
        "install-gitnexus",
        """
        npm config set fund false
        npm config set audit false
        npm config set update-notifier false
        npm config set legacy-peer-deps true
        retry 3 npm install -g gitnexus@latest
        command -v gitnexus
        gitnexus --version
        """,
    )


def step_clone_repo() -> str:
    return wrap_step(
        "clone-repo",
        """
        rm -rf "$TARGET"
        mkdir -p /opt
        git clone --depth 1 "$REPO_URL" "$TARGET"
        chown -R "$SSH_USER:$SSH_USER" "$TARGET"
        sudo -u "$SSH_USER" git config --global --add safe.directory "$TARGET" || true
        chmod -R a+rX "$TARGET"
        ln -sfn "$TARGET" /home/ubuntu/project
        chown -h ubuntu:ubuntu /home/ubuntu/project
        if id -u "$SSH_USER" >/dev/null 2>&1; then
          ln -sfn "$TARGET" "/home/$SSH_USER/project"
          chown -h "$SSH_USER:$SSH_USER" "/home/$SSH_USER/project" || true
        fi
        ls -la "$TARGET"
        """,
    )


def step_configure_claude_mcp(claude_template: str) -> str:
    template_body = claude_template.rstrip("\n")
    return (
        'echo "=== STEP: configure-claude-mcp ==="\n'
        'if id -u "$SSH_USER" >/dev/null 2>&1; then\n'
        '  HOME_DIR="$(user_home "$SSH_USER")"\n'
        '  cat > "$HOME_DIR/.claude.json" <<'"'"'EOF_CLAUDE'"'"'\n'
        f"{template_body}\n"
        'EOF_CLAUDE\n'
        '  chown "$SSH_USER:$SSH_USER" "$HOME_DIR/.claude.json"\n'
        '  sudo -u "$SSH_USER" env HOME="$HOME_DIR" python3 -c "import json, os; from pathlib import Path; p = Path(os.environ['"'"'HOME'"'"']) / '"'"'.claude.json'"'"'; data = json.loads(p.read_text(encoding='"'"'utf-8'"'"')); print(json.dumps(data.get('"'"'projects'"'"', {}).get('"'"'/opt/project'"'"', {}).get('"'"'mcpServers'"'"', {}), indent=2))"\n'
        'fi\n'
        'echo "=== DONE: configure-claude-mcp ==="'
    )



def step_write_orchestrator_files(goals_md: str, run_script: str, post_upload_script: str) -> str:
    goals_body = goals_md.rstrip("\n")
    run_body = run_script.rstrip("\n")
    post_upload_body = post_upload_script.rstrip("\n")
    return (
        'echo "=== STEP: write-orchestrator-files ==="\n'
        'mkdir -p "$ORCH_DIR"\n'
        'chown -R "$SSH_USER:$SSH_USER" "$ORCH_DIR"\n'
        'cat > "$ORCH_DIR/GOALS.md" <<'"'"'EOF_GOALS'"'"'\n'
        f"{goals_body}\n"
        'EOF_GOALS\n'
        'chown "$SSH_USER:$SSH_USER" "$ORCH_DIR/GOALS.md"\n'
        'cat > "$ORCH_DIR/run.sh" <<'"'"'EOF_RUN'"'"'\n'
        f"{run_body}\n"
        'EOF_RUN\n'
        'chmod +x "$ORCH_DIR/run.sh"\n'
        'chown "$SSH_USER:$SSH_USER" "$ORCH_DIR/run.sh"\n'
        'cat > "$ORCH_DIR/post_upload.sh" <<'"'"'EOF_POST'"'"'\n'
        f"{post_upload_body}\n"
        'EOF_POST\n'
        'chmod +x "$ORCH_DIR/post_upload.sh"\n'
        'chown "$SSH_USER:$SSH_USER" "$ORCH_DIR/post_upload.sh"\n'
        'echo "=== DONE: write-orchestrator-files ==="'
    )


def step_start_orchestrator() -> str:
    return wrap_step(
        "start-orchestrator",
        """
        BOOTSTRAP_PANE_LOG="$ORCH_DIR/orchestrator-pane.log"
        sudo -u "$SSH_USER" tmux kill-session -t orchestrator 2>/dev/null || true
        sudo -u "$SSH_USER" tmux new-session -d -s orchestrator \
          "timeout ${RUNTIME_MINUTES}m bash -lc '/opt/orchestrator/run.sh; /opt/orchestrator/post_upload.sh'"

        for _ in $(seq 1 45); do
          if sudo -u "$SSH_USER" tmux has-session -t orchestrator 2>/dev/null; then
            if sudo -u "$SSH_USER" tmux capture-pane -p -t orchestrator | grep -q 'Yes, I accept'; then
              sudo -u "$SSH_USER" tmux send-keys -t orchestrator Down
              sleep 2
              sudo -u "$SSH_USER" tmux send-keys -t orchestrator Enter
              sleep 2
              sudo -u "$SSH_USER" tmux send-keys -t orchestrator Enter
              break
            fi
          else
            break
          fi
          sleep 1
        done

        if sudo -u "$SSH_USER" tmux has-session -t orchestrator 2>/dev/null; then
          sudo -u "$SSH_USER" tmux capture-pane -p -t orchestrator > "$BOOTSTRAP_PANE_LOG" || true
        fi
        """,
    )



def startup_epilogue() -> str:
    return 'echo "=== startup-script done: $(date -Is) ==="'


def build_startup_script(repo_url: str, ssh_user: str, runtime_minutes: int, goals_md: str, claude_template: str) -> str:
    run_script = build_orchestrator_script(DISCORD_WEBHOOK_URL)
    post_upload_script = build_post_upload_script(DISCORD_WEBHOOK_URL)
    steps = [
        startup_preamble(repo_url, ssh_user, runtime_minutes),
        step_install_base_packages(),
        step_install_node(),
        step_install_claude(),
        step_install_gitnexus(),
        step_clone_repo(),
        step_configure_claude_mcp(claude_template),
        step_write_orchestrator_files(goals_md, run_script, post_upload_script),
        step_start_orchestrator(),
        startup_epilogue(),
    ]
    return "\n\n".join(steps).strip() + "\n"


def validate_startup_script(script: str):
    commands = (["bash", "-n"], ["wsl", "bash", "-n"])
    for command in commands:
        try:
            result = subprocess.run(command, input=script, text=True, capture_output=True, check=False)
        except FileNotFoundError:
            continue
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "bash syntax validation failed")
        return
    raise RuntimeError("No bash executable available for startup script validation")


def create_api_instance(repo_url: str, instance_name: str, replace: bool, runtime_minutes: int):
    instance_client = compute_v1.InstancesClient()

    if replace:
        delete_instance_if_exists(instance_client, instance_name)

    goals_md = load_goals_md()
    claude_template = load_claude_template()
    startup_script = build_startup_script(repo_url, SSH_USER, runtime_minutes, goals_md, claude_template)

    instance = compute_v1.Instance(
        name=instance_name,
        machine_type=f"zones/{ZONE}/machineTypes/e2-standard-4",
        disks=[
            compute_v1.AttachedDisk(
                boot=True,
                auto_delete=True,
                initialize_params=compute_v1.AttachedDiskInitializeParams(
                    source_image="projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64"
                ),
            )
        ],
        network_interfaces=[
            compute_v1.NetworkInterface(
                network="global/networks/default",
                access_configs=[compute_v1.AccessConfig(name="External NAT")],
            )
        ],
        metadata=compute_v1.Metadata(
            items=[compute_v1.Items(key="startup-script", value=startup_script)]
        ),
    )

    print(f"[*] Creating {instance_name} cloning {repo_url} ...")
    op = instance_client.insert(project=PROJECT_ID, zone=ZONE, instance_resource=instance)
    wait_for_zone_op(PROJECT_ID, ZONE, op.name)
    print("[*] Instance create operation complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("repo_url", nargs="?", help="Public git repo URL to clone")
    parser.add_argument("--name", default=DEFAULT_INSTANCE_NAME)
    parser.add_argument("--replace", action="store_true", help="Delete instance if it already exists")
    parser.add_argument("--runtime-minutes", type=int, default=15, help="Max runtime for orchestrator (minutes)")
    parser.add_argument("--print-startup-script", action="store_true", help="Render the startup script to stdout and exit")
    parser.add_argument("--write-startup-script", help="Write the rendered startup script to a file and exit")
    parser.add_argument("--validate-startup-script", action="store_true", help="Render the startup script and run a local bash syntax check")
    args = parser.parse_args()

    if args.print_startup_script or args.write_startup_script or args.validate_startup_script:
        if not args.repo_url:
            parser.error("repo_url is required when rendering or validating the startup script")
        rendered = build_startup_script(args.repo_url, SSH_USER, args.runtime_minutes, load_goals_md(), load_claude_template())
        if args.print_startup_script:
            print(rendered, end="")
        if args.write_startup_script:
            Path(args.write_startup_script).write_text(rendered, encoding="utf-8")
            print(f"Wrote startup script to {args.write_startup_script}")
        if args.validate_startup_script:
            validate_startup_script(rendered)
            print("Startup script syntax OK")
    else:
        if not args.repo_url:
            parser.error("repo_url is required unless using a render/validate flag")
        create_api_instance(args.repo_url, args.name, args.replace, args.runtime_minutes)










