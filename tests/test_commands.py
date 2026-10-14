from framewire.commands import match_command, normalize_phrase
from framewire.lipmatch import best_match, dtw_distance


def test_commands_match_unicode_phrases_exactly():
    commands = {"Licht an": "lamp.on", "encIende la luz": "lamp.on"}
    assert match_command("  LICHT, AN!  ", commands) == "lamp.on"
    assert match_command("Enciende la luz.", commands) == "lamp.on"
    assert match_command("licht an bitte", commands) is None
    assert normalize_phrase("日本語 コマンド") == "日本語 コマンド"


def test_visual_templates_reject_stillness_and_ambiguous_labels():
    sample = [[0.1 + (i % 5) * 0.04, 0.3] for i in range(15)]
    history = [[0.1, 0.3]] * 10 + sample
    assert dtw_distance(sample, sample) == 0
    assert best_match(history, {"lamp.on": [sample]}) == ("lamp.on", 0.0)
    assert best_match([[0.1, 0.3]] * 20, {"lamp.on": [sample]}) is None
    assert best_match(history, {"a": [sample], "b": [sample]}) is None
