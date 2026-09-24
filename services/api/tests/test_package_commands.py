import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "cli",
)))

from meshagent_cli.package_commands import parse_installs, pinned_installs


def simplified(command: str):
    return [
        (item.name, item.version, item.ecosystem, item.manager, item.exact)
        for item in parse_installs(command)
    ]


def test_compound_commands_only_parse_install_segments():
    command = (
        "cd /tmp && head README.md && tail log.txt; git show HEAD | cat "
        "&& python3 -m pip install httpx==0.27.2 requests"
    )
    assert simplified(command) == [
        ("httpx", "0.27.2", "PyPI", "pip", True),
        ("requests", "", "PyPI", "pip", False),
    ]


def test_non_install_commands_never_become_packages():
    for command in (
        "head README.md", "tail -f app.log", "cd src", "git show HEAD",
        "pip show httpx", "npm view react", "python -m pip list",
    ):
        assert parse_installs(command) == []


def test_supported_python_manager_grammars():
    assert simplified("pip install flask==3.0.0") == [
        ("flask", "3.0.0", "PyPI", "pip", True),
    ]
    assert simplified("uv pip install httpx==0.27.2") == [
        ("httpx", "0.27.2", "PyPI", "uv", True),
    ]
    assert simplified("uv add pydantic==2.9.2") == [
        ("pydantic", "2.9.2", "PyPI", "uv", True),
    ]
    assert simplified("poetry add requests@2.32.3") == [
        ("requests", "2.32.3", "PyPI", "poetry", True),
    ]
    assert simplified("pip install 'urllib3>=2,<3'") == [
        ("urllib3", "", "PyPI", "pip", False),
    ]


def test_supported_npm_manager_grammars_and_scoped_packages():
    assert simplified("npm i react@18.3.1 @scope/pkg@1.2.3 lodash") == [
        ("react", "18.3.1", "npm", "npm", True),
        ("@scope/pkg", "1.2.3", "npm", "npm", True),
        ("lodash", "", "npm", "npm", False),
    ]
    assert simplified("yarn add zod@3.23.8") == [
        ("zod", "3.23.8", "npm", "yarn", True),
    ]


def test_flags_and_their_values_are_not_packages():
    assert simplified(
        "sudo env PIP_NO_CACHE_DIR=1 pip install --upgrade "
        "--index-url https://example.invalid/simple numpy==1.26.4"
    ) == [("numpy", "1.26.4", "PyPI", "pip", True)]
    assert parse_installs("pip install -r requirements.txt") == []
    assert parse_installs("npm install --registry https://registry.invalid") == []


def test_indirect_and_dynamic_sources_are_not_invented_as_packages():
    for command in (
        "pip install ./dist/pkg.whl",
        "pip install git+https://example.invalid/repo.git",
        "pip install $PACKAGE",
        "npm install https://example.invalid/pkg.tgz",
        "bash -c 'pip install numpy==1.26.4'",
    ):
        assert parse_installs(command) == []


def test_only_exact_versions_are_post_execution_package_evidence():
    command = "pip install requests numpy==1.26.4 && npm i react@18.3.1 react@latest"
    assert [(item.name, item.version) for item in pinned_installs(command)] == [
        ("numpy", "1.26.4"), ("react", "18.3.1"),
    ]
