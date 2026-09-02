from youtube_downloader.cli import build_parser, options_from_args


def test_skip_existing_is_the_default_resume_mode():
    args = build_parser().parse_args(["--url", "https://www.youtube.com/watch?v=abc"])
    options = options_from_args(args)
    assert options.skip_existing and not options.overwrite


def test_overwrite_resume_mode_disables_skip():
    args = build_parser().parse_args([
        "--url", "https://www.youtube.com/watch?v=abc", "--overwrite",
    ])
    options = options_from_args(args)
    assert options.overwrite and not options.skip_existing
