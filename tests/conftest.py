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
            "proposals_dir": str(tmp_path / "nope" / "proposals"),
        },
    })
    monkeypatch.setenv("CEC_HUB_INTEGRATIONS", str(cfg))
    monkeypatch.delenv("CEC_HUB_SOPS_DIR", raising=False)
    app_module = _reload_app()
    with app_module.app.test_client() as client:
        yield client


SAMPLE_LENSES_CSV = (
    "brand,lens,index,type,blank_mm,sph_min,sph_max,cyl_max,price,notes\n"
    "Hoya,Nulux 1.50,1.50,stock,70,-4.00,+4.00,-2.00,18.50,\n"
    "Hoya,Stellify 1.55,1.55,stock,65,+0.25,+4.00,-2.00,24.00,plus powers only\n"
    "Hoya,Stellify 1.50,1.50,stock,75,-6.00,+4.00,-2.00,21.00,\n"
    "Hoya,Nulux 1.60,1.60,stock,70,-8.00,+6.00,-2.00,32.00,\n"
    "Hoya,SV Grind 1.50,1.50,grind,,-10.00,+8.00,-4.00,45.00,made to order\n"
)


@pytest.fixture
def hub_client_lenses(tmp_path, monkeypatch):
    """Test client pointed at a tmp lenses folder with a small Hoya file."""
    lenses_dir = tmp_path / "lenses"
    lenses_dir.mkdir()
    (lenses_dir / "hoya.csv").write_text(SAMPLE_LENSES_CSV, encoding="utf-8")
    (lenses_dir / "_template.csv").write_text(
        "lens,sph_min,sph_max\nIGNORED EXAMPLE,-1.00,+1.00\n", encoding="utf-8")
    monkeypatch.setenv("CEC_HUB_LENSES_DIR", str(lenses_dir))
    monkeypatch.delenv("CEC_HUB_INTEGRATIONS", raising=False)
    monkeypatch.delenv("CEC_HUB_SOPS_DIR", raising=False)
    app_module = _reload_app()
    with app_module.app.test_client() as client:
        yield client, lenses_dir


@pytest.fixture
def hub_client_lens_jobs(tmp_path, monkeypatch):
    """Test client with a tmp lens catalogue AND an agent lens-jobs file."""
    lenses_dir = tmp_path / "lenses"
    lenses_dir.mkdir()
    (lenses_dir / "hoya.csv").write_text(SAMPLE_LENSES_CSV, encoding="utf-8")

    jobs_path = tmp_path / "agent-logs" / "lens-jobs.jsonl"
    jobs_path.parent.mkdir(parents=True)
    jobs_path.write_text(
        '{"job": "31655", "entered": "2026-07-10 14:32", "supplier": "Eye CU",'
        ' "code": "SE15HC", "stk_grd": "Grd",'
        ' "right": {"sph": -3.0, "cyl": -1.0}, "left": {"sph": -2.75},'
        ' "frame": {"a": 52, "dbl": 18, "pd": 62}}\n'
        '{"job": "31656", "entered": "2026-07-10 15:01", "supplier": "Hoya",'
        ' "stk_grd": "Stk", "right": {"sph": -9.0}, "left": {"sph": -9.25}}\n',
        encoding="utf-8",
    )
    cfg = _write_integrations(tmp_path, {
        "optomate_agent": {"lens_jobs": str(jobs_path)},
    })
    monkeypatch.setenv("CEC_HUB_LENSES_DIR", str(lenses_dir))
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
