"""PyCharm's interpreter for a task: a pixi env in the task's clone.

PyCharm keeps its interpreters in one global file, jdk.table.xml in its settings
directory; a project only names one, in .idea/misc.xml. A task gets an entry of its own,
`pixi <project>/<task>`, a Conda interpreter at the env like the one PyCharm makes itself,
and its project names it. PyCharm takes the entry up whether it runs or not.

An env that is not there yet is installed in the background first; PyCharm, already open,
gets the interpreter once that is done.

The bytearmy root is one of PyCharm's trusted locations, in trusted-paths.xml, so PyCharm
opens the clones below it without asking whether to trust them.
"""

from __future__ import annotations

import logging
import plistlib
import re
import shutil
import subprocess
import sys
import threading
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path

log = logging.getLogger(__name__)

APP = Path("/Applications/PyCharm.app")
SDK_TYPE = "Python SDK"
TABLE = "jdk.table.xml"
TRUSTED = "trusted-paths.xml"
PIXI_ENV = re.compile(r"\.pixi/envs/([^/]+)/?")
_lock = threading.Lock()  # one writer of jdk.table.xml at a time


def interpreter_name(project: str, task: str) -> str:
    return f"pixi {project}/{task}"


def give_interpreter(
    repo: Path, env_path: str, name: str, env: Mapping[str, str], home: Path | None = None
) -> None:
    """Make the pixi env at `env_path` (relative to `repo`) the interpreter of the PyCharm
    project in `repo`. An env that is not installed yet is installed in the background, with
    `env` as the environment of pixi; a repository without a pixi manifest gets nothing."""
    target = repo / env_path
    if (target / "bin" / "python").exists():
        _write(repo, target, name, home)
        return
    match = PIXI_ENV.fullmatch(env_path)
    if match is None or not _has_pixi_manifest(repo):
        return
    threading.Thread(
        target=_install_then_write,
        args=(repo, target, match[1], name, dict(env), home),
        daemon=True,
    ).start()


def remove_interpreter(name: str, home: Path | None = None) -> None:
    """Take the entry `name` out of PyCharm's interpreters, if it is there."""
    settings = _settings_dir(home or Path.home())
    if settings is None or not (settings / TABLE).exists():
        return
    try:
        with _lock:
            tree = ET.parse(settings / TABLE)
            table = tree.getroot().find("component[@name='ProjectJdkTable']")
            found = [jdk for jdk in _entries(table) if _name(jdk) == name]
            if not found:
                return
            for jdk in found:
                table.remove(jdk)
            _save(tree, settings / TABLE, declaration=False)
    except (OSError, ET.ParseError) as error:
        log.warning("Could not remove the interpreter %s: %s", name, error)


def trust(root: Path, home: Path | None = None) -> None:
    """Make `root`, and so every directory below it, a trusted location of PyCharm."""
    home = home or Path.home()
    settings = _settings_dir(home)
    if settings is None:
        log.warning("No settings directory of PyCharm found; %s stays untrusted.", root)
        return
    try:
        _add_trusted(settings / TRUSTED, root, home)
    except (OSError, ET.ParseError) as error:
        log.warning("Could not make %s a trusted location of PyCharm: %s", root, error)


def _has_pixi_manifest(repo: Path) -> bool:
    if (repo / "pixi.toml").exists():
        return True
    pyproject = repo / "pyproject.toml"
    return pyproject.exists() and "[tool.pixi" in pyproject.read_text()


def _install_then_write(
    repo: Path, target: Path, env_name: str, name: str, env: dict[str, str], home: Path | None
) -> None:
    pixi = shutil.which("pixi", path=env.get("PATH")) or str(Path.home() / ".pixi/bin/pixi")
    try:
        done = subprocess.run(
            [pixi, "install", "--environment", env_name],
            cwd=repo,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        log.warning("pixi install in %s failed: %s", repo, error)
        return
    if done.returncode != 0:
        log.warning("pixi install in %s failed: %s", repo, done.stderr.strip()[-500:])
        return
    _write(repo, target, name, home)


def _write(repo: Path, target: Path, name: str, home: Path | None) -> None:
    settings = _settings_dir(home or Path.home())
    if settings is None:
        log.warning("No settings directory of PyCharm found; %s gets no interpreter.", repo)
        return
    try:
        with _lock:
            _add_entry(settings / TABLE, name, repo, target)
        _name_in_project(repo / ".idea" / "misc.xml", name)
    except (OSError, ET.ParseError) as error:
        log.warning("Could not give %s its interpreter: %s", repo, error)


def _settings_dir(home: Path) -> Path | None:
    """PyCharm's settings directory: of the installed app on macOS, else the newest."""
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support" / "JetBrains"
        try:
            with (APP / "Contents" / "Info.plist").open("rb") as info:
                version = plistlib.load(info)["CFBundleShortVersionString"]
            return base / f"PyCharm{'.'.join(version.split('.')[:2])}" / "options"
        except (OSError, KeyError, plistlib.InvalidFileException):
            pass
    else:
        base = home / ".config" / "JetBrains"
    versions = [
        directory
        for directory in base.glob("PyCharm*")
        if re.fullmatch(r"PyCharm\d+\.\d+", directory.name)
    ]
    if not versions:
        return None
    newest = max(versions, key=lambda d: tuple(map(int, d.name[7:].split("."))))
    return newest / "options"


def _entries(table: ET.Element | None) -> list[ET.Element]:
    return [] if table is None else table.findall("jdk")


def _name(jdk: ET.Element) -> str | None:
    element = jdk.find("name")
    return None if element is None else element.get("value")


def _add_entry(table_file: Path, name: str, repo: Path, target: Path) -> None:
    """Put a Conda interpreter `name` at `target` into the table, in place of one so named."""
    if table_file.exists():
        tree = ET.parse(table_file)
        # PyCharm's own file: the version before bytearmy first changed it is kept.
        backup = table_file.with_name(f"{TABLE}.bytearmy-backup")
        if not backup.exists():
            backup.write_bytes(table_file.read_bytes())
    else:
        tree = ET.ElementTree(ET.Element("application"))
    table = tree.getroot().find("component[@name='ProjectJdkTable']")
    if table is None:
        table = ET.SubElement(tree.getroot(), "component", name="ProjectJdkTable")
    for jdk in _entries(table):
        if _name(jdk) == name:
            table.remove(jdk)

    jdk = ET.SubElement(table, "jdk", version="2")
    ET.SubElement(jdk, "name", value=name)
    ET.SubElement(jdk, "type", value=SDK_TYPE)
    ET.SubElement(jdk, "homePath", value=str(target / "bin" / "python"))
    # PyCharm finds the roots itself.
    roots = ET.SubElement(jdk, "roots")
    ET.SubElement(ET.SubElement(roots, "classPath"), "root", type="composite")
    ET.SubElement(ET.SubElement(roots, "sourcePath"), "root", type="composite")
    additional = ET.SubElement(
        jdk,
        "additional",
        ASSOCIATED_PROJECT_PATH=str(repo),
        WORKING_DIRECTORY=str(repo),
        SDK_UUID=str(uuid.uuid4()),
    )
    ET.SubElement(additional, "setting", name="FLAVOR_ID", value="Conda")
    flavor = (
        '{"env":{"envIdentity":{"sealedClassChildId":"NamedEnv","data":'
        f'{{"envName":"{target.name}","userReadableName":"{target.name}"}}}},'
        f'"fullCondaPathOnTarget":"{target / "libexec" / "conda"}"}}}}'
    )
    ET.SubElement(additional, "setting", name="FLAVOR_DATA", value=flavor)
    _save(tree, table_file, declaration=False)


def _add_trusted(trusted_file: Path, root: Path, home: Path) -> None:
    """Put `root` into the trusted locations, unless it is there already."""
    if trusted_file.exists():
        tree = ET.parse(trusted_file)
    else:
        tree = ET.ElementTree(ET.Element("application"))
    component = tree.getroot().find("component[@name='Trusted.Paths.Settings']")
    if component is None:
        component = ET.SubElement(tree.getroot(), "component", name="Trusted.Paths.Settings")
    option = component.find("option[@name='TRUSTED_PATHS']")
    if option is None:
        option = ET.SubElement(component, "option", name="TRUSTED_PATHS")
    paths = option.find("list")
    if paths is None:
        paths = ET.SubElement(option, "list")
    # PyCharm writes paths below the home directory with the macro $USER_HOME$.
    known = {
        Path(entry.get("value", "").replace("$USER_HOME$", str(home)))
        for entry in paths.findall("option")
    }
    if root in known:
        return
    if trusted_file.exists():
        # PyCharm's own file: the version before bytearmy first changed it is kept.
        backup = trusted_file.with_name(f"{TRUSTED}.bytearmy-backup")
        if not backup.exists():
            backup.write_bytes(trusted_file.read_bytes())
    ET.SubElement(paths, "option", value=str(root))
    _save(tree, trusted_file, declaration=False)


def _name_in_project(misc_file: Path, name: str) -> None:
    """Let the project in .idea use the interpreter `name`; its other settings stay."""
    if misc_file.exists():
        tree = ET.parse(misc_file)
    else:
        tree = ET.ElementTree(ET.Element("project", version="4"))
    manager = tree.getroot().find("component[@name='ProjectRootManager']")
    if manager is None:
        manager = ET.SubElement(tree.getroot(), "component", name="ProjectRootManager")
    manager.set("version", "2")
    manager.set("project-jdk-name", name)
    manager.set("project-jdk-type", SDK_TYPE)
    _save(tree, misc_file, declaration=True)


def _save(tree: ET.ElementTree, file: Path, declaration: bool) -> None:
    ET.indent(tree, space="  ")
    file.parent.mkdir(parents=True, exist_ok=True)
    tree.write(file, encoding="UTF-8", xml_declaration=declaration)
