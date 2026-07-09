"""Fixtures for the CEC Hub test suite.

Everything is mocked onto tmp_path — no test ever touches the real review
bot, the real Optomate agent, or the network. The app is reloaded per
fixture so the CEC_HUB_* environment overrides take effect, mirroring the
referral tool's test style.
"""

import importlib
import json
from datetime import date, timedelta

import pytest


def _reload_app():
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    return app_module


def _write_integrations(tmp_path, paths: dict):
    cfg_path = tmp_path / "integrations.json"
    cfg_path.write_text(json.dumps(paths), encoding="utf-8")
    return cfg_path


@pytest.fixture
def connected_world(tmp_path):
    """A tmp folder pretending to be the review bot + Optomate agent."""
    today = date.today()

    # --- review bot files ---
    bot_dir = tmp_path / "review-bot"
    bot_dir.mkdir()
    sent_log = {
        "101": {"last_sent": str(today - timedelta(days=1)), "appointment_type": "general"},
        "102": {"last_sent": str(today - timedelta(days=2)), "appointment_type": "ortho_k"},
        "103": {"last_sent": str(today - timedelta(days=6)), "appointment_type": "general"},
        "104": {"last_sent": str(today - timedelta(days=10)), "appointment_type": "general"},
        "105": {"last_sent": str(today - timedelta(days=40)), "appointment_type": "dry_eye"},
    }
    (bot_dir / "sent_log.json").write_text(json.dumps(sent_log), encoding="utf-8")
    (bot_dir / "config.json").write_text(json.dumps({"enabled": True}), encoding="utf-8")
    (bot_dir / "review_bot.log").write_text(
        "2026-07-06 08:00:00 [INFO] Review bot started\n"
        "2026-07-06 08:00:02 [INFO] Review bot finished. Sent: 2, Skipped: 1, Errors: 0\n"
        "2026-07-07 08:00:00 [INFO] Review bot started\n"
        "2026-07-07 08:00:03 [INFO] Review bot finished. Sent: 4, Skipped: 2, Errors: 0\n",
        encoding="utf-8",
    )

    # --- Optomate agent files ---
    agent_logs = tmp_path / "agent" / "logs"
    agent_logs.mkdir(parents=True)
    digest_lines = [f"2026-07-{(i % 8) + 1:02d} order line {i}" for i in range(250)]
    (agent_logs / "orders-digest.log").write_text("\n".join(digest_lines), encoding="utf-8")
    (agent_logs / "uncollected-ready.txt").write_text(
        "JOB 4411 — frames — ready since 03/07\nJOB 4415 — contact lenses — ready since 05/07\n",
        encoding="utf-8",
    )

    proposals = tmp_path / "agent" / "inventory" / "proposals"
    proposals.mkdir(parents=True)
    (proposals / "frames-restock.csv").write_text(
        "SKU,Description,Qty,Supplier\n"
        "F-1001,Ray-Ban RB5154 49,2,Luxottica\n"
        "F-2044,Tomato Glasses TKAC12,3,Tomato Glasses\n"
        "F-3080,Hoya frame demo,1,Hoya\n",
        encoding="utf-8",
    )
    (proposals / "cl-solutions.approved.csv").write_text(
        "SKU,Description,Qty\nCL-77,AOSept Plus 360ml,12\n",
        encoding="utf-8",
    )

    return {
        "root": tmp_path,
        "proposals": proposals,
        "paths": {
            "review_bot": {
                "sent_log": str(bot_dir / "sent_log.json"),
                "bot_log": str(bot_dir / "review_bot.log"),
                "config": str(bot_dir / "config.json"),
            },
            "optomate_agent": {
                "orders_digest_log": str(agent_logs / "orders-digest.log"),
                "uncollected_ready": str(agent_logs / "uncollected-ready.txt"),
                "proposals_dir": str(proposals),
            },
        },
    }


@pytest.fixture
def hub_client(tmp_path, monkeypatch, connected_world):
    """Test client with every integration 'connected' via tmp files."""
    cfg = _write_integrations(tmp_path, connected_world["paths"])
    monkeypatch.setenv("CEC_HUB_INTEGRATIONS", str(cfg))
    monkeypatch.delenv("CEC_HUB_SOPS_DIR", raising=False)  # real seeded SOPs
    app_module = _reload_app()
    with app_module.app.test_client() as client:
        yield client


@pytest.fixture
def hub_client_disconnected(tmp_path, monkeypatch):
    """Test client on a machine where NONE of the other systems exist."""
    cfg = _write_integrations(tmp_path, {
        "review_bot": {
            "sent_log": str(tmp_path / "nope" / "sent_log.json"),
            "bot_log": str(tmp_path / "nope" / "review_bot.log"),
            "config": str(tmp_path / "nope" / "config.json"),
        },
        "optomate_agent": {
            "orders_digest_log": str(tmp_path / "nope" / "orders-digest.log"),
            "uncollected_ready": str(tmp_path / "nope" / "uncollected-ready.txt"),
            "proposals_dir": str(tmp_path / "nope" / "proposals"),
        },
    })
    monkeypatch.setenv("CEC_HUB_INTEGRATIONS", str(cfg))
    monkeypatch.delenv("CEC_HUB_SOPS_DIR", raising=False)
    app_module = _reload_app()
    with app_module.app.test_client() as client:
        yield client


@pytest.fixture
def hub_client_custom_sops(tmp_path, monkeypatch):
    """Test client pointed at a tmp SOP folder (for image serving etc.)."""
    sops = tmp_path / "sops"
    (sops / "images").mkdir(parents=True)
    (sops / "images" / "till.png").write_bytes(b"\x89PNG\r\n\x1a\nfakepng")
    (sops / "test-guide.md").write_text(
        "---\ncategory: Testing\nupdated: 2026-07-09\nowner: Mark\n---\n\n"
        "# Test guide\n\nA guide used only by the tests.\n\n"
        "1. Do the first thing.\n\n"
        "![The till](images/till.png)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CEC_HUB_SOPS_DIR", str(sops))
    monkeypatch.delenv("CEC_HUB_INTEGRATIONS", raising=False)
    app_module = _reload_app()
    with app_module.app.test_client() as client:
        yield client
