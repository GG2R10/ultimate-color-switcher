import os

from color_switcher.backend import detect_diff


def test_route_a_first_run(fake_project):
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()

    result = detect_diff.detect_with_route(config)
    assert result["route"] == "a"
    assert len(result["colors"]) == 1
    assert os.path.isfile(config.detected_palette_csv)


def test_route_b_unchanged(fake_project):
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()

    detect_diff.detect_with_route(config)  # first run persists the baseline
    result = detect_diff.detect_with_route(config)  # same content again
    assert result["route"] == "b"


def test_route_c_when_colors_change(fake_project):
    fp = fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()
    detect_diff.detect_with_route(config)

    with open(fp, "a") as f:
        f.write("\n#222222")

    result = detect_diff.detect_with_route(config)
    assert result["route"] == "c"
    assert {c["color"] for c in result["diff"]["added"]} == {"222222"}
    assert result["diff"]["removed"] == []


def test_route_c_when_a_color_disappears(fake_project):
    fp = fake_project.make_file("a.css", "#111111 #222222")
    config = fake_project.load_config()
    detect_diff.detect_with_route(config)

    with open(fp, "w") as f:
        f.write("#111111")

    result = detect_diff.detect_with_route(config)
    assert result["route"] == "c"
    assert {c["color"] for c in result["diff"]["removed"]} == {"222222"}
    assert result["diff"]["added"] == []


def test_save_false_does_not_overwrite_saved_detection(fake_project):
    fake_project.make_file("a.css", "#111111")
    config = fake_project.load_config()
    detect_diff.detect_with_route(config)
    before = open(config.detected_palette_csv).read()

    fake_project.make_file("a.css", "#111111 #333333")  # would change the detection
    detect_diff.detect_with_route(config, save=False)

    after = open(config.detected_palette_csv).read()
    assert before == after
