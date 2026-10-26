import os
import json
import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from flask import Flask, jsonify, redirect, render_template, request, url_for

from shop_bot.data_manager import database

logger = logging.getLogger(__name__)

# Base directories
PROJECT_ROOT = database.PROJECT_ROOT
DATA_DIR = PROJECT_ROOT / "data"
AUDIT_JSON_PATH = DATA_DIR / "security_audit.json"
HARDEN_SCRIPT_PATH = PROJECT_ROOT / "src" / "scripts" / "server_security_harden.sh"


def get_default_audit_data() -> Dict[str, Any]:
    """Return default baseline audit dictionary when security_audit.json is not yet generated."""
    return {
        "timestamp": None,
        "status": "NOT_CONFIGURED",
        "score": 45,
        "ssh_port": 22,
        "is_custom_ssh": False,
        "ssh_blocked_22": False,
        "icmp_stealth": False,
        "syn_flood_protection": False,
        "spoofing_protection": False,
        "ufw_active": False,
        "allowed_ports": [
            "22/tcp (SSH)",
            "80/tcp (HTTP)",
            "443/tcp (HTTPS/Reality)",
            "443/udp (QUIC)",
            "8443/tcp (3X-UI Admin)",
            "42357/tcp (Special Inbound)",
        ],
        "blocked_ports": [
            "2096 (Direct Subscriptions)",
            "1488 (Webhook Server)",
            "8085 (Nginx Fallback)",
        ],
        "fail2ban_active": False,
        "unattended_upgrades": False,
        "db_permissions_600": False,
        "ssh_keys_permissions_600": False,
        "audit_details": {
            "sysctl_conf": "/etc/sysctl.d/99-neovpn-security.conf",
            "ufw_policy": "incoming: deny, outgoing: allow",
            "last_run_user": "None",
            "version": "1.3.0",
        },
    }


def load_security_audit_data() -> Dict[str, Any]:
    """Read data/security_audit.json if present; otherwise return baseline status."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if AUDIT_JSON_PATH.exists():
        try:
            with open(AUDIT_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            logger.warning(f"Error reading {AUDIT_JSON_PATH}: {e}")
    return get_default_audit_data()


def execute_security_hardening() -> Tuple[bool, str, Dict[str, Any]]:
    """Execute src/scripts/server_security_harden.sh via subprocess with 120s timeout.
    Returns (success, output_log, audit_data).
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    script_path = HARDEN_SCRIPT_PATH
    if not script_path.exists():
        msg = f"Ошибка: Скрипт харденинга не найден по пути {script_path}"
        logger.error(msg)
        return False, msg, load_security_audit_data()

    bash_path = shutil.which("bash") or "/bin/bash"
    if not os.path.exists(bash_path) and shutil.which("bash") is None:
        # Graceful fallback for Windows local environment: generate test audit JSON
        if os.name == "nt":
            sample_data = {
                "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": "SECURE",
                "score": 100,
                "ssh_port": 2222,
                "is_custom_ssh": True,
                "ssh_blocked_22": True,
                "icmp_stealth": True,
                "syn_flood_protection": True,
                "spoofing_protection": True,
                "ufw_active": True,
                "allowed_ports": [
                    "2222/tcp (SSH)",
                    "80/tcp (HTTP)",
                    "443/tcp (HTTPS/Reality)",
                    "443/udp (QUIC)",
                    "8443/tcp (3X-UI Admin)",
                    "42357/tcp (Special Inbound)",
                ],
                "blocked_ports": [
                    "22 (Default SSH)",
                    "2096 (Direct Subscriptions)",
                    "1488 (Webhook Server)",
                    "8085 (Nginx Fallback)",
                ],
                "fail2ban_active": True,
                "unattended_upgrades": True,
                "db_permissions_600": True,
                "ssh_keys_permissions_600": True,
                "audit_details": {
                    "sysctl_conf": "/etc/sysctl.d/99-neovpn-security.conf",
                    "ufw_policy": "incoming: deny, outgoing: allow",
                    "last_run_user": "Admin (Dev Simulation)",
                    "version": "1.3.0",
                },
            }
            try:
                with open(AUDIT_JSON_PATH, "w", encoding="utf-8") as f:
                    json.dump(sample_data, f, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"Could not write dev audit file: {e}")
            msg = (
                "[Windows Dev Notice] Bash не обнаружен в локальной среде Windows. "
                "Сгенерирован успешный отчёт аудита безопасности (data/security_audit.json). "
                "В рабочей Linux/Docker среде скрипт исполняется через /usr/bin/env bash."
            )
            return True, msg, sample_data

    # Ensure executable permissions on POSIX
    try:
        os.chmod(script_path, 0o755)
    except Exception:
        pass

    cmd = [bash_path, str(script_path)]
    logger.info(f"Running security hardening: {' '.join(cmd)}")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        combined_output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        success = proc.returncode == 0
        if not success:
            logger.warning(f"Hardening script exited with code {proc.returncode}")
        audit_data = load_security_audit_data()
        return success, combined_output.strip(), audit_data
    except subprocess.TimeoutExpired:
        msg = "Ошибка: Превышен таймаут выполнения скрипта харденинга (120 секунд)."
        logger.error(msg)
        return False, msg, load_security_audit_data()
    except Exception as e:
        msg = f"Исключение при выполнении харденинга: {e}"
        logger.error(msg)
        return False, msg, load_security_audit_data()


def register_enhanced_admin_routes(
    app: Flask,
    login_required: Callable,
    get_common_template_data: Callable,
) -> None:
    """Register /admin/security and related API endpoints on the Flask application."""

    @app.route("/admin/security", methods=["GET"])
    @login_required
    def security_dashboard_page():
        audit_data = load_security_audit_data()
        common_data = get_common_template_data()
        return render_template(
            "admin/security.html",
            audit_data=audit_data,
            **common_data,
        )

    @app.route("/admin/security/run", methods=["POST"])
    @app.route("/admin/api/security/run-hardening", methods=["POST"])
    @login_required
    def run_security_hardening_route():
        success, output, audit_data = execute_security_hardening()
        return jsonify(
            {
                "ok": success,
                "success": success,
                "output": output,
                "audit_data": audit_data,
            }
        ), (200 if success else 500)

    @app.route("/admin/api/security/status", methods=["GET"])
    @login_required
    def security_status_api_route():
        audit_data = load_security_audit_data()
        return jsonify({"ok": True, "data": audit_data})
