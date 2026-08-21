"""First-run health checks.

Each check returns {"ok": bool, "detail": str} so the UI can show a specific
remediation rather than a raw traceback.
"""
import subprocess
from typing import Dict


def check_sensei() -> Dict:
    try:
        import sensei  # noqa: F401
    except ImportError:
        return {"ok": False, "detail": "sensei-review is not installed"}
    return {"ok": True, "detail": "importable"}


def check_gitlab() -> Dict:
    try:
        from sensei.config import load_config
        import gitlab

        config = load_config()
        gl = gitlab.Gitlab(config["gitlab_url"], private_token=config["gitlab_pat"])
        gl.auth()
        return {"ok": True, "detail": "authenticated as %s" % gl.user.username}
    except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
        return {"ok": False, "detail": str(exc)[:200]}


def check_claude() -> Dict:
    try:
        proc = subprocess.run(
            ["claude", "-p", "--output-format", "text"],
            input="reply with OK",
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        return {"ok": False, "detail": "claude CLI not found on PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "claude timed out"}

    if proc.returncode != 0:
        return {
            "ok": False,
            "detail": "claude failed (%s). Run `claude /login`."
            % proc.stderr.strip()[:120],
        }
    return {"ok": True, "detail": "authenticated"}


def health() -> Dict:
    return {
        "sensei": check_sensei(),
        "gitlab": check_gitlab(),
        "claude": check_claude(),
    }
