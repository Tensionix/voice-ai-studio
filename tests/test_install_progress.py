from system_core.ui.install_progress import InstallProgressTracker, parse_install_progress


def test_parse_powershell_download_progress():
    progress = parse_install_progress("Turbo model: 1.43 GB / 1.51 GB (94.2%) @ 34.4 MB/s")

    assert progress is not None
    assert progress.label == "Turbo model"
    assert progress.percent == 94.2
    assert progress.done_text == "1.43 GB"
    assert progress.total_text == "1.51 GB"
    assert progress.speed_text == "34.4 MB/s"
    assert progress.eta_text == "0:02"


def test_parse_audion_step_progress():
    progress = parse_install_progress("[audion-step] 3/7 Preload GigaAM v3 payloads")

    assert progress is not None
    assert 42.0 < progress.percent < 43.0
    assert progress.done_text == "3"
    assert progress.total_text == "7"
    assert progress.label == "Preload GigaAM v3 payloads"


def test_tracker_computes_pip_raw_speed_and_eta():
    ticks = iter([0.0, 1.0])
    tracker = InstallProgressTracker(clock=lambda: next(ticks))

    assert tracker.update("Downloading torch-2.6.0+cu124.whl (2.5 GB)") is None
    assert tracker.update("Progress 0 of 1073741824") is not None
    progress = tracker.update("Progress 268435456 of 1073741824")

    assert progress is not None
    assert progress.label == "torch-2.6.0+cu124.whl"
    assert progress.percent == 25.0
    assert progress.done_text == "256.0 MB"
    assert progress.total_text == "1.00 GB"
    assert progress.speed_text == "256.0 MB/s"
    assert progress.eta_text == "0:03"


def test_parse_localized_decimal_commas():
    progress = parse_install_progress("FFmpeg: 10,0 MB / 20,0 MB (50,0%) @ 5,0 MB/s")

    assert progress is not None
    assert progress.percent == 50.0
    assert progress.speed_text == "5,0 MB/s"
    assert progress.eta_text == "0:02"
