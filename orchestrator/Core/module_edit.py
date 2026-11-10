#!/usr/bin/env python3
"""
The "edit all files" step of a hardware module (the module wizard's last page, `llm-cli module create --edit`,
`llm-cli module edit <target>` and Build > Targets... > Edit Files...).

`ModuleEdit` holds the files of one target folder that a person may finish by hand: the four stage modules, the
slot modules its target.yml names in the `stages:` block, target.yml and every Dockerfile; never the framework's stage
runner (orchestrator/runtime_layer/runner.sh) nor a runner file a target ships (build.sh, stage_user.sh), never the
helper scripts or the crawled knowledge. Each file keeps the text it had when the step opened (the generator's
version for a new module, the saved one for an existing module), so its diff shows every change. `problems()` runs
the one check of a target folder (module_generator.validate_target_folder: the shell deny patterns, the placeholders,
the pinned images, the slots, bash -n) on a copy with the edits, and `save()` writes nothing while a check fails.

An AI's proposal enters as a whole file text (the GUI tools fill a file's field) or as a unified diff
(`apply_diff`); either is only a change in this step, which the person saves or drops. Nothing here saves on its
own.

A new module is made in a private scratch folder outside the targets folder (`stage_folder`) and moved into the
targets folder only when the person saves it (`commit_staged`), so a module being edited is never a target a build
could pick up.

A module made from the general template records its variables (the formats of target.yml's `template_variables`,
orchestrator/schemas/target_template.py): the step shows them first (`variables`), and a changed value
(`set_variables`) makes the files that hold them anew from the template (target.yml's record and capabilities, the
config stage's block) as edits with their diff, checked like any edit; the files stay the fallback. A repair may
propose changed variable values the same way (`variables_diff`).
"""

import difflib
import os
import re
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import yaml

from orchestrator.utils.logging import get_logger
from orchestrator.Core.module_generator import (SHIPPED_TEMPLATE, STAGE_MODULES, module_slug, target_name_collision,
                                                validate_target_folder)

# The runner of every build is the framework's runtime layer; a target's own runner files are ignored by builds
# (runtime_layer.own_runner_note). None of them is ever offered for editing.
RUNNER_FILES = ("runner.sh", "build.sh", "stage_user.sh")
# The marks of target.yml's metadata that only a run on the board changes, never an edit (a build's result is kept
# beside target.yml: Core/module_test.py).
MARKS = ("untested", "provenance")
STAGING_PREFIX = "llmcf-module-edit-"


class EditRefused(ValueError):
    """Saving was refused: `problems` names every failing check."""

    def __init__(self, problems: List[str]):
        self.problems = list(problems)
        super().__init__("The module was not saved, a check failed:\n- " + "\n- ".join(self.problems))


def template_for(targets_dir: Path) -> Path:
    """The template a target folder's lines are compared with: the targets folder's own, else the shipped one."""
    own = Path(targets_dir) / "_template"
    return own if own.is_dir() else SHIPPED_TEMPLATE


def _slot_modules(folder: Path) -> List[str]:
    """The module names target.yml puts into its stages: slots (unreadable or malformed: none)."""
    try:
        meta = yaml.safe_load((folder / "target.yml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    block = meta.get("stages") if isinstance(meta, dict) else None
    names: List[str] = []
    if isinstance(block, dict):
        for value in block.values():
            for name in (value if isinstance(value, list) else ([] if value in (None, "") else [value])):
                name = str(name)
                if re.fullmatch(r"[a-z0-9_]{1,64}", name) and name not in names:
                    names.append(name)
    elif block is None and (folder / "modules" / "rotate_module.sh").is_file():
        names.append("rotate")   # a target without the block keeps the rotate slot (runtime_layer.stage_order)
    return names


def editable_files(folder: Path) -> List[str]:
    """The files of a target folder the edit step lists, in the order of a build: the stage modules with the slot
    modules, target.yml, the Dockerfiles. Only files that exist; never a runner file."""
    folder = Path(folder)
    found: List[str] = []
    for name in [*STAGE_MODULES, *_slot_modules(folder)]:
        rel = f"modules/{name}_module.sh"
        if (folder / rel).is_file() and not (folder / rel).is_symlink() and rel not in found:
            found.append(rel)
    if (folder / "target.yml").is_file():
        found.append("target.yml")
    found += sorted(p.name for p in folder.iterdir()
                    if p.is_file() and not p.is_symlink() and p.name.lower().startswith("dockerfile"))
    return [rel for rel in found if Path(rel).name not in RUNNER_FILES]


def _marks(text: str) -> Dict[str, object]:
    try:
        meta = (yaml.safe_load(text) or {}).get("metadata") or {}
    except (yaml.YAMLError, AttributeError):
        return {}
    return {k: meta.get(k) for k in MARKS if isinstance(meta, dict) and k in meta}


def _write_lf(path: Path, text: str) -> None:
    """Writes the text exactly as given (no line-end translation), atomically, keeping the file's mode."""
    from orchestrator.utils.atomic import replace_file
    mode = os.stat(path).st_mode & 0o7777 if path.exists() else None
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None and os.name != "nt":
            os.chmod(tmp, mode)
        replace_file(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class ModuleEdit:
    """The editable files of the target folder `folder` with the text each had when the step opened."""

    def __init__(self, folder: Path, template_dir: Optional[Path] = None):
        self.folder = Path(folder)
        if not (self.folder / "target.yml").is_file():
            raise ValueError(f"{self.folder.name}: no target folder (target.yml is missing)")
        self.template_dir = Path(template_dir) if template_dir else template_for(self.folder.parent)
        self.files: List[str] = editable_files(self.folder)
        self.generated: Dict[str, str] = {rel: (self.folder / rel).read_text(encoding="utf-8", errors="replace")
                                          for rel in self.files}
        self.texts: Dict[str, str] = dict(self.generated)
        # A repair of a failed build (Core/module_test.py): {"how": "by hand" | "proposal", "by": who proposed,
        # "summary"}; None: an edit. A save records it in the module's build test record.
        self.repair: Optional[Dict[str, str]] = None

    # --- the texts -------------------------------------------------------------------------------------------------

    def _known(self, rel: str) -> str:
        rel = str(rel).replace("\\", "/")
        if rel not in self.texts:
            if Path(rel).name in RUNNER_FILES:
                raise ValueError(f"{rel}: the stage runner is the framework's and is never edited here")
            raise ValueError(f"{rel}: not a file of this edit step (it edits: {', '.join(self.files)})")
        return rel

    def text(self, rel: str) -> str:
        return self.texts[self._known(rel)]

    def set_text(self, rel: str, text: str) -> None:
        self.texts[self._known(rel)] = str(text)

    def reset(self, rel: Optional[str] = None) -> None:
        """Back to the text the file had when the step opened (one file, or all)."""
        for name in ([self._known(rel)] if rel else self.files):
            self.texts[name] = self.generated[name]

    def changed(self) -> List[str]:
        return [rel for rel in self.files if self.texts[rel] != self.generated[rel]]

    # --- the template's variables ----------------------------------------------------------------------------------

    def _meta(self) -> Dict:
        try:
            meta = yaml.safe_load(self.texts.get("target.yml", "")) or {}
        except yaml.YAMLError as e:
            raise ValueError(f"target.yml is no valid YAML now ({str(e).splitlines()[0][:200]})") from None
        if not isinstance(meta, dict):
            raise ValueError("target.yml is no mapping now")
        return meta

    def variables(self) -> Optional[str]:
        """The module's template variables as the step shows them first: the formats of target.yml's
        template_variables as YAML text (as target.yml holds them now). None for a module that records none (a module
        made before the general template, or a shipped target): its files are the step then."""
        try:
            record = self._meta().get("template_variables")
        except ValueError:
            return None
        if not isinstance(record, dict):
            return None
        if record.get("source_images"):   # the formats and the files taken from source images, as one block
            return yaml.safe_dump({"formats": record.get("formats") or [], "source_images": record["source_images"]},
                                  sort_keys=False, allow_unicode=True)
        return yaml.safe_dump(record.get("formats") or [], sort_keys=False, allow_unicode=True)

    def set_variables(self, formats) -> List[str]:
        """Takes changed template variables (YAML or JSON text of the formats, or a list): target.yml's
        template_variables and the formats of its capabilities, and the config stage's block, are made anew from the
        template with them, as edits of those files (their diff shows the change). Returns the files it changed.
        ValueError with the reasons when a value fails its checks, when the module records no variables or its block
        is gone, or when a format would need a file the module does not have (the step adds no file: the module is
        made again then)."""
        from orchestrator.Core.module_generator import (FORMATS_BEGIN, FORMATS_END, format_problems, parse_formats,
                                                        render_formats, source_image_problems)
        from orchestrator.schemas.target_template import FormatVariables
        try:
            given = yaml.safe_load(formats) if isinstance(formats, str) and formats.strip() else formats
        except yaml.YAMLError:
            given = None   # parse_formats below names the YAML error
        # A block with source_images changes them too; formats alone keep the module's source images.
        images_given = isinstance(given, dict) and "source_images" in given
        images, image_problems = source_image_problems(given.get("source_images") if images_given else None)
        values, problems = format_problems(parse_formats(formats))
        problems = problems + image_problems
        if problems:
            raise ValueError("The variables were not taken:\n- " + "\n- ".join(problems))
        if not values:
            raise ValueError("The variables declare no format; a module builds at least one (gguf, for example).")
        meta = self._meta()
        if not isinstance(meta.get("template_variables"), dict):
            raise ValueError(f"{self.folder.name} records no template variables (it was made before the general "
                             "template); change its files instead.")
        config_rel = "modules/config_module.sh"
        text = self.texts.get(config_rel, "")
        begin, end = text.find(FORMATS_BEGIN), text.find(FORMATS_END)
        if begin < 0 or end < begin:
            raise ValueError(f"{config_rel} has no block of the template variables any more; restore it (Reset) "
                             "before changing the variables.")
        needed = [rel for f in values for rel in FormatVariables.model_validate(f).helper_files()]
        missing = sorted({rel for rel in needed if not (self.folder / rel).is_file()})
        if missing:
            raise ValueError(f"These formats need {', '.join(missing)}, which {self.folder.name} does not have; the "
                             "edit step adds no file: make the module again with these formats.")
        caps = meta.get("capabilities") if isinstance(meta.get("capabilities"), dict) else {}
        caps["formats"] = {f["id"]: FormatVariables.model_validate(f).capability() for f in values}
        meta["capabilities"] = caps
        record = {"schema_version": 1, "formats": values}
        kept = images if images_given else (meta["template_variables"].get("source_images") or [])
        if kept:
            record["source_images"] = kept
        meta["template_variables"] = record
        before = dict(self.texts)
        self.texts[config_rel] = text[:begin] + render_formats(values) + text[end + len(FORMATS_END):]
        self.texts["target.yml"] = yaml.dump(meta, default_flow_style=False, sort_keys=False)
        return [rel for rel in self.files if self.texts[rel] != before[rel]]

    def diff(self, rel: Optional[str] = None) -> str:
        """The unified diff of one file, or of every changed file, against the text it had when the step opened."""
        names = [self._known(rel)] if rel else self.changed()
        out: List[str] = []
        for name in names:
            lines = difflib.unified_diff(self.generated[name].splitlines(keepends=True),
                                         self.texts[name].splitlines(keepends=True), f"a/{name}", f"b/{name}")
            for line in lines:
                out.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
        return "".join(out)

    def apply_diff(self, diff_text: str) -> List[str]:
        """Applies a unified diff (a proposal of an AI or a person) to the current texts, all or nothing. Returns
        the files it changed. ValueError when a file is not one of this step (the runner never is), when a file would
        be added or deleted, or when a hunk does not match the current text."""
        patches = _parse_diff(diff_text)
        if not patches:
            raise ValueError("the diff changes no file (no '--- a/...' and '+++ b/...' lines with hunks)")
        new_texts = dict(self.texts)
        for old_name, new_name, hunks in patches:
            if "/dev/null" in (old_name, new_name):
                raise ValueError(f"{new_name if old_name == '/dev/null' else old_name}: the edit step neither adds "
                                 "nor deletes a file")
            if old_name != new_name:
                raise ValueError(f"{old_name} -> {new_name}: the edit step does not rename files")
            rel = self._known(old_name)
            new_texts[rel] = _apply_hunks(rel, new_texts[rel], hunks)
        changed = [rel for rel in self.files if new_texts[rel] != self.texts[rel]]
        self.texts = new_texts
        return changed

    # --- the checks and the save -----------------------------------------------------------------------------------

    def problems(self) -> List[str]:
        """Every failing check of the folder with the edits (empty: none), run on a copy, so nothing is written."""
        problems: List[str] = []
        if "target.yml" in self.texts:
            before, after = _marks(self.generated["target.yml"]), _marks(self.texts["target.yml"])
            problems += [f"target.yml: the mark metadata.{k} ({before[k]}) may not be removed or changed here; the "
                         "framework sets it (untested: a run on the board)" for k in before if after.get(k) != before[k]]
        with tempfile.TemporaryDirectory(prefix="llmcf-module-check-") as scratch:
            copy = Path(scratch) / self.folder.name
            shutil.copytree(self.folder, copy, symlinks=True)
            for rel in self.changed():
                _write_lf(copy / rel, self.texts[rel])
            problems += validate_target_folder(copy, self.template_dir)
        return problems

    def save(self) -> List[str]:
        """Writes the changed files after the checks passed (EditRefused with the reasons otherwise). Returns the
        files written; afterwards the saved texts are what later diffs compare with."""
        problems = self.problems()
        if problems:
            raise EditRefused(problems)
        written = self.changed()
        for rel in written:
            _write_lf(self.folder / rel, self.texts[rel])
            self.generated[rel] = self.texts[rel]
        if written:
            self._record_change(written)
        return written

    def _record_change(self, written: List[str]) -> None:
        """A saved repair or edit of a module in its build test record (untested until a build passes); a record that
        cannot be written never undoes the save."""
        from orchestrator.Core.module_test import record_change
        repair = self.repair or {}
        try:
            record_change(self.folder, files=written, how=repair.get("how", "edit"), by=repair.get("by", "person"),
                          summary=repair.get("summary", ""))
        except Exception as e:  # noqa: BLE001 - the files are saved; only the history line is missing
            get_logger(__name__).warning(f"The repair of {self.folder.name} was not recorded: {e}")


def variables_diff(folder: Path, formats, template_dir: Optional[Path] = None) -> str:
    """The unified diff that changed template variables make of a module's files (ModuleEdit.set_variables on its
    saved files): how a repair proposes changed variable values. ValueError as set_variables."""
    session = ModuleEdit(Path(folder), template_dir)
    session.set_variables(formats)
    return session.diff()


# --- a new module in a scratch folder ---------------------------------------------------------------------------------

def stage_folder() -> Path:
    """A private scratch folder (outside the targets folder) to make a new module in before it is saved."""
    return Path(tempfile.mkdtemp(prefix=STAGING_PREFIX))


def name_taken(targets_dir, module_name: str) -> Optional[str]:
    """The existing target a new module's name would capture (target_name_collision; one targets folder or several),
    None when it is free."""
    return target_name_collision(targets_dir, module_slug(module_name), module_name)


def commit_staged(folder: Path, targets_dir: Path, others: Iterable[Path] = ()) -> Path:
    """Moves a module made in a scratch folder into the targets folder (never over an existing target, also not one
    of the other targets folders `others`) and removes the scratch folder. Returns the module's folder in the targets
    folder."""
    folder, targets_dir = Path(folder), Path(targets_dir)
    taken = target_name_collision([targets_dir, *others], folder.name)
    if taken:
        raise ValueError(f"A target named '{taken}' exists now; the module was not saved over it.")
    targets_dir.mkdir(parents=True, exist_ok=True)
    dest = targets_dir / folder.name
    shutil.move(str(folder), str(dest))
    discard_staged(folder)
    return dest


def discard_staged(folder: Optional[Path]) -> None:
    """Removes the scratch folder a module was made in (its parent, made by stage_folder)."""
    if folder is None:
        return
    folder = Path(folder)
    root = folder.parent if folder.parent.name.startswith(STAGING_PREFIX) else folder
    if root.name.startswith(STAGING_PREFIX) and root.exists():
        from orchestrator.utils.atomic import remove_tree
        left = remove_tree(root, label="the edit step's scratch folder")
        if left:   # a file that could not be removed stays in the system's temporary folder, said in the log
            get_logger(__name__).warning(f"The edit step's scratch folder was not removed completely: {root}")


# --- unified diffs ----------------------------------------------------------------------------------------------------

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _strip_prefix(name: str) -> str:
    name = name.split("\t", 1)[0].strip()
    if name == "/dev/null":
        return name
    return name[2:] if name[:2] in ("a/", "b/") else name


def _parse_diff(text: str) -> List[tuple]:
    """[(old name, new name, [(old start, [hunk lines])])] of a unified diff."""
    patches: List[tuple] = []
    lines = str(text).splitlines(keepends=True)
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            old, new = _strip_prefix(line[4:]), _strip_prefix(lines[i + 1][4:])
            hunks: List[tuple] = []
            i += 2
            while i < len(lines) and lines[i].startswith("@@"):
                m = _HUNK.match(lines[i])
                if not m:
                    raise ValueError(f"not a hunk header: {lines[i].strip()[:80]}")
                old_count = int(m.group(2)) if m.group(2) is not None else 1
                new_count = int(m.group(4)) if m.group(4) is not None else 1
                body: List[str] = []
                i += 1
                seen_old = seen_new = 0
                while i < len(lines) and (seen_old < old_count or seen_new < new_count or
                                          lines[i].startswith("\\")):
                    current = lines[i]
                    if current.startswith("\\"):
                        if body:   # "\ No newline at end of file": the line before has no line end
                            body[-1] = body[-1].rstrip("\n").rstrip("\r")
                        i += 1
                        continue
                    tag = current[:1]
                    if tag not in (" ", "-", "+") and current.strip() == "":
                        current, tag = " " + current, " "   # an empty context line some tools write without a space
                    if tag not in (" ", "-", "+"):
                        raise ValueError(f"the hunk at line {m.group(1)} of {old} is cut short")
                    seen_old += tag in (" ", "-")
                    seen_new += tag in (" ", "+")
                    body.append(current)
                    i += 1
                hunks.append((int(m.group(1)), body))
            patches.append((old, new, hunks))
            continue
        i += 1
    return patches


def _apply_hunks(rel: str, text: str, hunks: Iterable[tuple]) -> str:
    lines = text.splitlines(keepends=True)
    offset = 0
    for start, body in hunks:
        old = [b[1:] for b in body if b[:1] in (" ", "-")]
        new = [b[1:] for b in body if b[:1] in (" ", "+")]
        at = max(start - 1, 0) + offset if old else start + offset
        if lines[at:at + len(old)] != old:
            found = [k for k in range(len(lines) - len(old) + 1) if lines[k:k + len(old)] == old] if old else []
            if len(found) != 1:
                raise ValueError(f"{rel}: the diff does not match the file's current text near line {start}")
            at = found[0]
        lines[at:at + len(old)] = new
        offset += len(new) - len(old)
    return "".join(lines)


__all__ = ["EditRefused", "MARKS", "ModuleEdit", "RUNNER_FILES", "commit_staged", "discard_staged", "editable_files",
           "name_taken", "stage_folder", "template_for", "variables_diff"]
