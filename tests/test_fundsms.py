"""Health fund texts tile: input validation and the not-connected path (no agent calls)."""
import json

from hub import fundsms


def test_preview_refuses_unknown_touch():
    assert "error" in fundsms.preview({}, "T0")


def test_send_needs_touch_and_fingerprint():
    assert "error" in fundsms.send({}, "HF9", "abcdef12")
    assert "fingerprint" in fundsms.send({}, "HF1", "not-a-hash")["error"]
    assert "Bad number" in fundsms.send({}, "HF1", "abcdef12", "x")["error"]


def test_not_connected_without_agent(tmp_path):
    cfg = {"optomate_agent": {"agent_dir": str(tmp_path)}}
    assert fundsms.preview(cfg, "HF1")["connected"] is False


def test_deselect_writes_only_validated_file(tmp_path):
    (tmp_path / "local-reports").mkdir()
    cfg = {"optomate_agent": {"agent_dir": str(tmp_path)}}
    assert fundsms.set_deselected(cfg, "HF1", 2026, ["3", 1, 3]) == {"ok": True, "deselected": 2}
    saved = json.loads((tmp_path / "local-reports" / "fundsms-deselect-2026-HF1.json").read_text())
    assert saved["pids"] == [1, 3]
    assert "error" in fundsms.set_deselected(cfg, "../x", 2026, [])
    assert "error" in fundsms.set_deselected(cfg, "HF1", "1999", [])
