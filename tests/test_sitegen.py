import json
import pathlib
import shutil

from matebot.sitegen import generate

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def test_generate_site(tmp_path):
    shots = tmp_path / "shots"
    shots.mkdir()
    shutil.copy(FIXTURES / "000004.slog", shots / "000004.slog")
    (shots / "000004.json").write_text(json.dumps({
        "id": "4", "rating": 4, "beanType": "Test Bean", "doseIn": "18",
        "doseOut": "36.1", "ratio": "2.01", "grindSetting": "2", "balanceTaste": "balanced",
        "notes": "hello",
    }))
    # a corrupted (gzip) notes file must be skipped, not crash
    (shots / "000005.json").write_bytes(b"\x1f\x8b\x08\x00junk")

    out = tmp_path / "docs"
    count = generate(shots, out, title="Test Journal")
    assert count == 1

    index = json.loads((out / "index.json").read_text())
    assert index["title"] == "Test Journal"
    (entry,) = index["shots"]
    assert entry["id"] == "000004"
    assert entry["rating"] == 4
    assert entry["bean"] == "Test Bean"
    assert entry["peak_bar"] > 5

    shot = json.loads((out / "shots" / "000004.json").read_text())
    assert shot["header"]["profile"] == "Adaptive v2"
    assert len(shot["series"]["cp"]) == 215
    assert shot["series"]["t"][1] == 0.25
    assert shot["notes"]["beanType"] == "Test Bean"

    for asset in ("index.html", "app.js", "style.css"):
        assert (out / asset).exists()
    assert "cdn" not in (out / "index.html").read_text().lower()


def test_modern_journal_uses_recorded_times(make_slog, tmp_path):
    import json

    from matebot.sitegen import generate

    shots = tmp_path / 'shots'
    shots.mkdir()
    (shots / '000001.slog').write_bytes(make_slog())
    out = tmp_path / 'site'
    assert generate(shots, out) == 1
    payload = json.loads((out / 'shots' / '000001.json').read_text())
    assert payload['series']['t'] == [0, .375, 70]
    assert payload['series']['ct'] == [92, 92, 92]
    assert payload['header']['phases'] == [{'t': .375, 'name': 'Brew'}]
    assert json.loads((out / 'index.json').read_text())['shots'][0]['peak_bar'] == 8.5
