import pytest

import taskwire
from taskwire.cli import main


def test_version_prints_the_package_version(capsys):
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == taskwire.__version__


@pytest.mark.parametrize(
    ("command", "phase"),
    [("migrate", 1), ("check", 1), ("worker", 2), ("controller", 4)],
)
def test_planned_commands_fail_with_a_clear_message(capsys, command, phase):
    assert main([command]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not implemented" in captured.err
    assert f"Phase {phase}" in captured.err


def test_a_command_is_required():
    with pytest.raises(SystemExit) as info:
        main([])
    assert info.value.code == 2


def test_unknown_command_is_a_usage_error():
    with pytest.raises(SystemExit) as info:
        main(["frobnicate"])
    assert info.value.code == 2
