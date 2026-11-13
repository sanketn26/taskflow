from taskwire.__main__ import main


def test_version_command(capsys):
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip()


def test_unknown_command_fails(capsys):
    assert main(["run"]) == 2
