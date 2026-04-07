from trcc.conf import Settings


def test_save_and_load_gpu_slots(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("trcc.conf.CONFIG_PATH", str(config_path))
    slots = {"top": "0000:0f:00.0", "bottom": "0000:06:00.0"}
    Settings._save_gpu_slots(slots)
    assert Settings._get_saved_gpu_slots() == slots

def test_save_and_load_gpu_cycle_seconds(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("trcc.conf.CONFIG_PATH", str(config_path))
    Settings._save_gpu_cycle_seconds(10)
    assert Settings._get_saved_gpu_cycle_seconds() == 10

def test_gpu_cycle_seconds_default(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("trcc.conf.CONFIG_PATH", str(config_path))
    assert Settings._get_saved_gpu_cycle_seconds() == 5

def test_save_and_load_gpu_indicator_color(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("trcc.conf.CONFIG_PATH", str(config_path))
    Settings._save_gpu_indicator_color("#FF0000")
    assert Settings._get_saved_gpu_indicator_color() == "#FF0000"

def test_gpu_indicator_color_default(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("trcc.conf.CONFIG_PATH", str(config_path))
    assert Settings._get_saved_gpu_indicator_color() == "#0000FF"

def test_gpu_pci_slot_fallback(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("trcc.conf.CONFIG_PATH", str(config_path))
    Settings._save_gpu_pci_slot("0000:0f:00.0")
    assert Settings._get_saved_gpu_slots() == {}
    assert Settings._get_saved_gpu_pci_slot() == "0000:0f:00.0"
