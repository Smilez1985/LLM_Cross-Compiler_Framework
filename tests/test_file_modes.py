"""The named modes of tests/file_modes are exactly the octal modes their names say."""

from tests import file_modes


def test_every_named_mode_is_its_octal_value():
    names = [n for n in dir(file_modes) if n.startswith("MODE_")]
    assert len(names) == 11
    for name in names:
        assert getattr(file_modes, name) == int(name[len("MODE_"):], 8), name


def test_the_framework_modes_are_the_octal_modes_they_describe():
    from orchestrator.utils import modes
    assert (modes.READ_ONLY, modes.READ_RUN_ONLY, modes.SHARED_READ, modes.SHARED_RUN, modes.NEW_FILE) == \
        (0o444, 0o555, 0o644, 0o755, 0o666)
