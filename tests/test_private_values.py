"""The module wizard's move of a private value (orchestrator/Core/private_values.py and the module generator;
docs/DESIGN.md, the wizard's local layer for private values).

A module is made to be shared: what is private to this computer (a path on a network drive, an internal domain, a key
file, a password) is taken out of its shell fields into the person's own file, and the module holds ${LOCAL_NAME}. Every
value is made at run time from random characters, so this file holds no secret and no real address.
"""

import os
import random
import shutil
import stat
import string
import subprocess
from pathlib import Path

import pytest

from orchestrator.Core import modules_env, private_values as pv
from orchestrator.Core.module_generator import ModuleGenerator
from orchestrator.security import export_review

ALNUM = string.ascii_letters + string.digits


def word(seed, n=7):
    return "".join(random.Random(seed).choices(string.ascii_lowercase, k=n))


def secret(seed, n=14):
    return "".join(random.Random(seed).choices(ALNUM, k=n))


A = word("corp")
PW = "pw" + secret("pw", 10)
PATH = f"/mnt/nas/{A}/sdk"
KEY = f"~/.ssh/id_{A}"
HOST = f"git.{A}.internal"

POSIX_BASH = pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="needs a POSIX bash")


# --- what is found and what it becomes ---------------------------------------------------------------------------

CASES = [
    ("a path on a network drive", f"export SDK={PATH}", PATH, "LOCAL_PATH_1"),
    ("the key file of ssh -i", f"ssh -i {KEY} board true", KEY, "LOCAL_SSH_KEY_1"),
    ("an internal domain", f"ping {HOST}", HOST, "LOCAL_HOST_1"),
    ("a password flag", f"tool login --password {PW}", PW, "LOCAL_PASSWORD_1"),
    ("sshpass", f"sshpass -p {PW} ssh board true", PW, "LOCAL_PASSWORD_1"),
    ("an environment variable", f"export DB_PASSWORD={PW}", PW, "LOCAL_PASSWORD_1"),
    ("a key format", "export TOKEN=" + "hf" + "_" + secret("hf", 34), None, "LOCAL_SECRET_1"),
]


@pytest.mark.parametrize("label,text,value,name", CASES, ids=[c[0] for c in CASES])
def test_a_private_value_is_replaced_by_its_variable_and_handed_back(label, text, value, name):
    clean, values, moved = pv.extract({"setup_commands": text})
    assert "${" + name + "}" in clean["setup_commands"], label
    (found,) = values.values() if value is None else (values[name],)
    assert found not in clean["setup_commands"]
    if value is not None:
        assert found == value
    assert [m.name for m in moved] == [name] and moved[0].field == "setup_commands"


def test_the_report_names_the_variable_and_what_it_is_and_never_the_value():
    _, values, moved = pv.extract({"setup_commands": f"export SDK={PATH}\ntool --password {PW}"})
    text = pv.describe(moved, "t", "modules_env/t.env")
    for value in values.values():
        assert value not in text
    assert "LOCAL_PATH_1" in text and "LOCAL_PASSWORD_1" in text and "modules_env/t.env" in text


def test_only_the_shell_fields_are_changed():
    fields = {"description": f"the board of {PATH}", "cpu_flags": f"-I{PATH}", "packages": "libusb",
              "setup_commands": f"export SDK={PATH}", "quantization_logic": f"    Q4_K_M) cp {PATH}/x \"$OUT\" ;;"}
    clean, _, moved = pv.extract(fields)
    assert clean["description"] == fields["description"] and clean["cpu_flags"] == fields["cpu_flags"]
    assert clean["packages"] == "libusb"
    assert PATH not in clean["setup_commands"] and PATH not in clean["quantization_logic"]
    assert {m.field for m in moved} == {"setup_commands", "quantization_logic"}


def test_the_same_value_gets_one_variable_and_a_second_value_the_next_one():
    other = f"/mnt/nas/{A}/other"
    clean, values, moved = pv.extract({"setup_commands": f"cd {PATH} && cp {PATH} {other}"})
    assert clean["setup_commands"] == "cd ${LOCAL_PATH_1} && cp ${LOCAL_PATH_1} ${LOCAL_PATH_2}"
    assert values == {"LOCAL_PATH_1": PATH, "LOCAL_PATH_2": other} and len(moved) == 2


def test_a_longer_path_is_a_value_of_its_own():
    clean, values, _ = pv.extract({"setup_commands": f"cd {PATH} && cp {PATH}/a ."})
    assert values == {"LOCAL_PATH_1": PATH, "LOCAL_PATH_2": f"{PATH}/a"}
    assert clean["setup_commands"] == "cd ${LOCAL_PATH_1} && cp ${LOCAL_PATH_2} ."


def test_a_value_the_file_already_has_keeps_its_name_and_a_new_one_never_takes_a_used_name():
    existing = {"LOCAL_PATH_1": f"/mnt/nas/{A}/old", "LOCAL_PATH_2": PATH}
    clean, values, moved = pv.extract({"setup_commands": f"a {PATH} b /mnt/nas/{A}/new"}, existing)
    assert "${LOCAL_PATH_2}" in clean["setup_commands"] and "${LOCAL_PATH_3}" in clean["setup_commands"]
    assert values == {"LOCAL_PATH_3": f"/mnt/nas/{A}/new"} and [m.name for m in moved] == ["LOCAL_PATH_3"]


def test_what_is_already_a_variable_or_holds_no_secret_is_left_alone():
    text = "export SDK=${LOCAL_PATH_1}\ntool --password $DB_PASSWORD\ntool --password <your-password>\nmake -j4\n"
    assert pv.extract({"setup_commands": text}) == ({"setup_commands": text}, {}, [])


def test_extracting_twice_changes_nothing_the_second_time():
    clean, _, _ = pv.extract({"setup_commands": f"export SDK={PATH}\ntool --password {PW}"})
    assert pv.extract(clean)[2] == [] and pv.extract(clean)[0] == clean


def test_a_value_the_file_cannot_hold_stays_where_it_is_for_the_export_to_stop():
    block = "-----BEGIN OPENSSH " + "PRIVATE KEY-----\n" + secret("k", 60) + "\n-----END OPENSSH " + "PRIVATE KEY-----"
    clean, values, moved = pv.extract({"setup_commands": f"cat <<EOF\n{block}\nEOF"})
    assert values == {} and moved == [] and block in clean["setup_commands"]


# --- a shell still runs the same command ------------------------------------------------------------------------

@POSIX_BASH
@pytest.mark.parametrize("script", [
    "chpasswd() { cat; }\necho 'dev:%(pw)s' | chpasswd\n",
    "chpasswd() { cat; }\necho \"dev:%(pw)s\" | chpasswd\n",
    "tool() { printf '%%s|' \"$@\"; }\ntool login --password %(pw)s --path %(path)s\n",
    "tool() { printf '%%s|' \"$@\"; }\ntool --path '%(path)s'\n",
    "export DB_PASSWORD=%(pw)s\nprintf '%%s' \"$DB_PASSWORD\"\n",
], ids=["single quotes", "double quotes", "flag and path", "path in single quotes", "environment variable"])
def test_the_replaced_script_prints_what_the_original_printed(script):
    text = script % {"pw": PW, "path": PATH}
    clean, values, moved = pv.extract({"setup_commands": text})
    assert moved and not any(v in clean["setup_commands"] for v in values.values())
    original = subprocess.run(["bash", "-c", text], capture_output=True, text=True, timeout=30)
    replaced = subprocess.run(["bash", "-c", clean["setup_commands"]], capture_output=True, text=True, timeout=30,
                              env={**os.environ, **values})
    assert original.returncode == replaced.returncode == 0
    assert replaced.stdout == original.stdout


# --- the generator -----------------------------------------------------------------------------------------------

def made(tmp_path, **fields):
    state = tmp_path / "state"
    generator = ModuleGenerator(tmp_path / "targets", state_dir=state)
    (tmp_path / "targets").mkdir(exist_ok=True)
    folder = Path(generator.generate_module(dict(module_name="Move Demo", architecture="aarch64", sdk="none", **fields)))
    return generator, folder, state


def folder_text(folder: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in sorted(folder.rglob("*")) if p.is_file())


def test_a_module_holds_no_private_value_and_the_persons_file_holds_them(tmp_path):
    generator, folder, state = made(tmp_path, setup_commands=f"export SDK={PATH}\nexport DB_PASSWORD={PW}\n",
                                    quantization_logic=f"    Q4_K_M) cp {PATH}/x \"$OUT\" ;;")
    everything = folder_text(folder)
    assert PATH not in everything and PW not in everything
    assert "export SDK=${LOCAL_PATH_1}" in (folder / "modules" / "source_module.sh").read_text()
    assert modules_env.read(state, "move_demo") == {"LOCAL_PATH_1": PATH, "LOCAL_PASSWORD_1": PW,
                                                    "LOCAL_PATH_2": f"{PATH}/x"}
    assert {m.name for m in generator.last_moved} == {"LOCAL_PATH_1", "LOCAL_PASSWORD_1", "LOCAL_PATH_2"}
    assert modules_env.path_for(state, "move_demo").parent == state / "modules_env"
    assert not list(folder.rglob("*.env")), "no file of the values in the module folder"


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_the_persons_file_is_private(tmp_path):
    _, _, state = made(tmp_path, setup_commands=f"export SDK={PATH}\n")
    assert stat.S_IMODE(modules_env.path_for(state, "move_demo").stat().st_mode) == 0o600


def test_a_module_without_private_values_writes_no_file(tmp_path):
    generator, _, state = made(tmp_path, setup_commands="make -j4\n")
    assert generator.last_moved == [] and not (state / "modules_env").exists()


def test_a_module_that_is_refused_leaves_no_file(tmp_path):
    (tmp_path / "targets").mkdir()
    generator = ModuleGenerator(tmp_path / "targets", state_dir=tmp_path / "state")
    with pytest.raises(ValueError):
        generator.generate_module(dict(module_name="Move Demo", architecture="no-such-arch",
                                       setup_commands=f"export SDK={PATH}\n"))
    assert not (tmp_path / "state" / "modules_env").exists()


def test_the_values_never_reach_the_data_the_templates_are_filled_from(tmp_path, monkeypatch):
    seen = []
    real = ModuleGenerator._process_template

    def spy(self, name, target_dir, data, *a, **k):
        seen.append(repr(data))
        return real(self, name, target_dir, data, *a, **k)
    monkeypatch.setattr(ModuleGenerator, "_process_template", spy)
    made(tmp_path, setup_commands=f"export DB_PASSWORD={PW}\n")
    assert seen and not any(PW in text for text in seen)


def test_a_second_module_run_with_the_same_values_keeps_the_names(tmp_path):
    _, _, state = made(tmp_path, setup_commands=f"export SDK={PATH}\n")
    (tmp_path / "targets" / "move_demo").rename(tmp_path / "gone")
    generator, folder, _ = made(tmp_path, setup_commands=f"export SDK={PATH}\nexport DB_PASSWORD={PW}\n")
    assert modules_env.read(state, "move_demo") == {"LOCAL_PATH_1": PATH, "LOCAL_PASSWORD_1": PW}
    assert [m.name for m in generator.last_moved] == ["LOCAL_PASSWORD_1"]


def test_the_default_state_folder_is_the_frameworks_own(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "home"))
    (tmp_path / "targets").mkdir()
    generator = ModuleGenerator(tmp_path / "targets")
    generator.generate_module(dict(module_name="Move Demo", architecture="aarch64", sdk="none",
                                   setup_commands=f"export SDK={PATH}\n"))
    assert modules_env.read(tmp_path / "home", "move_demo") == {"LOCAL_PATH_1": PATH}


def test_the_export_of_such_a_module_has_nothing_to_stop_at(tmp_path):
    """The point of it all: nothing of the module's files is private by its shape any more."""
    _, folder, _ = made(tmp_path, setup_commands=f"export SDK={PATH}\nssh -i {KEY} board true\nping {HOST}\n"
                                                 f"tool login --password {PW}\n")
    flagged = []
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            flagged += [f"{path.name}:{f.line}: {f.kind}" for f in export_review.to_review(path.read_text(encoding="utf-8"))]
    assert flagged == []
