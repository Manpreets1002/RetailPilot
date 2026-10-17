"""Project setup: requirements, Docker and Compose files, the cities list, and imports that need a package.

These catch the problems that show up only when the project is built or deployed, such as a missing
dependency, a Dockerfile pointing at a file that was renamed, or two services claiming the same port.
"""

import ast
import importlib.metadata
import json
import re
import sys
import tomllib
from urllib.parse import urlparse

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

import support  # noqa: F401
from support import CRAWLER, ROOT, SRC

REQUIREMENT_FILES = sorted(ROOT.glob("requirement-*.txt"))  # mcp, backend, frontend, crawler
DOCKERFILE = ROOT / "Dockerfile"
COMPOSE_FILES = {"root": ROOT / "docker-compose.yml", "crawler": CRAWLER / "docker-compose.yml"}

# Import name -> distribution name, where they differ.
IMPORT_TO_DIST = {
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "sklearn": "scikit-learn",
    "langchain_core": "langchain-core",
    "langchain_google_genai": "langchain-google-genai",
    "pydantic_settings": "pydantic-settings",
    "curl_cffi": "curl-cffi",
}
# Modules this project defines itself, so they are not third-party packages.
LOCAL_MODULES = {"retailpilot", "web", "config", "mcp_connection", "mcp_server", "parsers", "http_client",
                 "storage", "crawler", "manage", "tests", "support", "sample_data", "conftest"}


def _read_requirement_file(path) -> list[str]:
    """Lines of one requirements file, with comments removed and `-r other.txt` includes followed."""
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-r "):
            lines += _read_requirement_file(path.parent / line[3:].strip())
        else:
            lines.append(line)
    return lines


def _requirement_lines() -> list[str]:
    lines = []
    for path in REQUIREMENT_FILES:
        lines += _read_requirement_file(path)
    return lines


def _declared() -> dict[str, Requirement]:
    return {canonicalize_name(Requirement(line).name): Requirement(line) for line in _requirement_lines()}


def _third_party_imports(folders=(SRC, CRAWLER)) -> set[str]:
    names = set()
    for folder in folders:
        for path in folder.rglob("*.py"):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names.add(node.module.split(".")[0])
    return names - set(sys.stdlib_module_names) - LOCAL_MODULES - {"__future__"}


# ------------------------------------------------------------------ requirement-*.txt

def test_packages_listed_in_several_files_agree_on_the_version():
    # Several services share a package (for example pymongo). Their version ranges must not contradict each other.
    specs: dict[str, set[str]] = {}
    for line in _requirement_lines():
        requirement = Requirement(line)
        specs.setdefault(canonicalize_name(requirement.name), set()).add(str(requirement.specifier))
    conflicting = {name: sorted(s) for name, s in specs.items() if len(s) > 1}
    assert conflicting == {}, f"Different version ranges for the same package: {conflicting}"


def test_every_pyproject_dependency_is_in_requirements():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = _declared()
    missing = [
        dep for dep in pyproject["project"]["dependencies"]
        if canonicalize_name(Requirement(dep).name) not in declared
    ]
    assert missing == [], f"pyproject.toml needs these but no requirement-*.txt file lists them: {missing}"


def test_every_third_party_import_is_declared():
    declared = _declared()
    missing = []
    for name in sorted(_third_party_imports()):
        dist = IMPORT_TO_DIST.get(name, name)
        if canonicalize_name(dist) not in declared:
            missing.append(f"{name} (package {dist})")
    assert missing == [], f"Imported in the code but missing from the requirement-*.txt files: {missing}"


@pytest.mark.parametrize("name", sorted(_declared()))
def test_installed_version_matches_requirement(name):
    requirement = _declared()[name]
    try:
        installed = importlib.metadata.version(requirement.name)
    except importlib.metadata.PackageNotFoundError:
        pytest.fail(f"{requirement.name} is not installed; run: pip install -r requirement-<service>.txt")
    assert requirement.specifier.contains(installed, prereleases=True), (
        f"{requirement.name} {installed} does not satisfy {requirement.specifier}"
    )


# Source folders each service's image copies, and the requirements file that service installs.
SERVICE_FILES = {
    "mcp": (["src/retailpilot/mcp", "src/retailpilot/config"], "requirement-mcp.txt"),
    "backend": (["src/retailpilot/backend", "src/retailpilot/agent", "src/retailpilot/mcp",
                 "src/retailpilot/config"], "requirement-backend.txt"),
    "frontend": (["src/retailpilot/frontend", "src/retailpilot/config"], "requirement-frontend.txt"),
    "crawler": (["amazon_ecomm_crawler", "src/retailpilot/config"], "requirement-crawler.txt"),
}


@pytest.mark.parametrize("service", sorted(SERVICE_FILES))
def test_each_service_file_covers_the_packages_its_code_imports(service):
    folders, filename = SERVICE_FILES[service]
    declared = {canonicalize_name(Requirement(line).name) for line in _read_requirement_file(ROOT / filename)}
    missing = []
    for name in sorted(_third_party_imports([ROOT / f for f in folders])):
        dist = IMPORT_TO_DIST.get(name, name)
        if canonicalize_name(dist) not in declared:
            missing.append(f"{name} (package {dist})")
    assert missing == [], f"{filename} does not list what the {service} code imports: {missing}"


# ------------------------------------------------------------------ Docker

def _dockerfile_stages() -> list[str]:
    return re.findall(r"^FROM\s+\S+\s+AS\s+(\w+)", DOCKERFILE.read_text(encoding="utf-8"), re.MULTILINE)


def test_dockerfile_files_it_copies_exist():
    text = DOCKERFILE.read_text(encoding="utf-8")
    referenced = set(re.findall(r"COPY\s+(requirement-[\w-]+\.txt)", text))
    referenced |= set(re.findall(r"pip install -r\s+(requirement-[\w-]+\.txt)", text))
    missing = sorted(name for name in referenced if not (ROOT / name).exists())
    assert missing == [], f"Dockerfile refers to files that do not exist: {missing}"


def test_dockerfile_copies_only_paths_that_exist():
    text = DOCKERFILE.read_text(encoding="utf-8")
    missing = []
    for args in re.findall(r"^COPY\s+(?!requirement)(.+)$", text, re.MULTILINE):
        *sources, _destination = args.split()  # the last word is the destination inside the image
        for path in sources:
            if not (ROOT / path).exists():
                missing.append(path)
    assert missing == [], f"Dockerfile COPY sources that do not exist: {missing}"


@pytest.mark.parametrize("compose_name", ["root", "crawler"])
def test_compose_build_targets_exist_as_dockerfile_stages(compose_name):
    stages = set(_dockerfile_stages())
    compose = yaml.safe_load(COMPOSE_FILES[compose_name].read_text(encoding="utf-8"))
    targets = {
        svc["build"]["target"]
        for svc in compose.get("services", {}).values()
        if isinstance(svc.get("build"), dict) and "target" in svc["build"]
    }
    assert targets <= stages, f"Compose targets {sorted(targets - stages)} are not Dockerfile stages {sorted(stages)}"


def _host_ports(compose_path) -> set[str]:
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    ports = set()
    for svc in compose.get("services", {}).values():
        for mapping in svc.get("ports", []):
            ports.add(str(mapping).split(":")[0])
    return ports


def test_root_compose_mongo_port_matches_env_mongo_uri():
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    mongo_port = urlparse(env["MONGO_URI"]).port
    assert str(mongo_port) in _host_ports(COMPOSE_FILES["root"]), (
        f".env MONGO_URI uses port {mongo_port}, but the root compose file publishes "
        f"{sorted(_host_ports(COMPOSE_FILES['root']))}"
    )


# ------------------------------------------------------------------ data files

def test_cities_json_has_ten_unique_cities_with_pincodes():
    cities = json.loads((CRAWLER / "cities.json").read_text(encoding="utf-8"))["cities"]
    assert len(cities) == 10
    assert len({c["slug"] for c in cities}) == 10
    for city in cities:
        assert {"slug", "name", "state", "pincode"} <= city.keys(), city
        assert re.fullmatch(r"\d{6}", str(city["pincode"])), city
