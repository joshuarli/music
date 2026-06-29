from music.ui import bold, clear_below, clear_line, colored, cursor_up, dim


def test_colored_basic():
    result = colored("hello", 196)
    assert "\033[38;5;196m" in result
    assert "hello" in result
    assert result.endswith("\033[0m")


def test_colored_bold():
    result = colored("hello", 42, bold=True)
    assert "\033[1m" in result
    assert "\033[38;5;42m" in result


def test_colored_dim():
    result = colored("hello", 240, dim=True)
    assert "\033[2m" in result
    assert "\033[38;5;240m" in result


def test_colored_bold_and_dim():
    result = colored("x", 100, bold=True, dim=True)
    assert "\033[1m" in result
    assert "\033[2m" in result
    # Bold before Dim in the current implementation
    idx_bold = result.index("\033[1m")
    idx_dim = result.index("\033[2m")
    assert idx_bold < idx_dim


def test_bold():
    result = bold("hello")
    assert result == "\033[1mhello\033[0m"


def test_dim():
    result = dim("hello")
    assert result == "\033[2mhello\033[0m"


def test_clear_line(capsys):
    clear_line()
    captured = capsys.readouterr()
    assert captured.out == "\033[K"


def test_cursor_up(capsys):
    cursor_up(3)
    captured = capsys.readouterr()
    assert captured.out == "\033[3A"


def test_clear_below(capsys):
    clear_below()
    captured = capsys.readouterr()
    assert captured.out == "\033[J"
