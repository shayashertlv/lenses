"""Mixed-directory collection must retain the agent tests' offline guards."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


REPO = Path(__file__).resolve().parents[2]
SCRUBBED = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "OPENAI_ADMIN_KEY",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
FAKE_VALUE = "offline-isolation-regression-placeholder"
IMPORTS = f"""
import asyncio.proactor_events
import asyncio.selector_events
import os
import socket
import httpx
import httpx2
from glasses3d.agent import budget

SCRUBBED = {SCRUBBED!r}
FAKE_VALUE = {FAKE_VALUE!r}
SOCKET_TARGETS = [(socket, name) for name in
    ('getaddrinfo', 'gethostbyname', 'gethostbyname_ex', 'create_connection')]
SOCKET_TARGETS += [(socket.socket, 'connect'), (socket.socket, 'connect_ex'),
    (asyncio.selector_events.BaseSelectorEventLoop, 'sock_connect'),
    (asyncio.proactor_events.BaseProactorEventLoop, 'sock_connect')]
TRANSPORTS = [httpx.AsyncHTTPTransport, httpx.HTTPTransport,
              httpx2.AsyncHTTPTransport, httpx2.HTTPTransport]
"""
AGENT_TEST = IMPORTS + """
def test_agent_isolation(request, tmp_path):
    # Looking up the fixture first would conceal a missing autouse attachment.
    assert 'offline_isolation' in request.fixturenames
    network, tripwire = request.getfixturevalue('offline_isolation')
    assert os.environ.get('G3D_OFFLINE') == '1'
    assert all(name not in os.environ for name in SCRUBBED)
    assert budget.PAID_RUN_LOCK_ROOT == tmp_path / 'paid-run-root'
    assert {(owner, name) for owner, name, _, _ in network._saved} == set(SOCKET_TARGETS)
    assert all(getattr(owner, name) is not original for owner, name, _, original in network._saved)
    assert {cls for cls, _ in tripwire._saved} == set(TRANSPORTS)
    assert all(cls.__init__ is not original for cls, original in tripwire._saved)
    assert network.attempts == [] and tripwire.constructions == []
"""
ROOT_TEST = IMPORTS + """
# Collection runs before any test fixtures: retain the actual unpatched methods.
ORIGINALS = [(owner, name, getattr(owner, name)) for owner, name in SOCKET_TARGETS]
ORIGINALS += [(cls, '__init__', cls.__init__) for cls in TRANSPORTS]
ORIGINAL_LOCK_ROOT = budget.PAID_RUN_LOCK_ROOT

def test_root_is_not_isolated(request):
    assert 'offline_isolation' not in request.fixturenames
    assert 'G3D_OFFLINE' not in os.environ
    assert all(os.environ.get(name) == FAKE_VALUE for name in SCRUBBED)
    assert budget.PAID_RUN_LOCK_ROOT == ORIGINAL_LOCK_ROOT
    assert all(getattr(owner, name) is original for owner, name, original in ORIGINALS)
"""


@pytest.mark.parametrize("interpositions", [1, 2])
def test_agent_isolation_survives_root_file_interpositions(tmp_path, interpositions):
    suite = tmp_path / "suite"
    agent = suite / "agent"
    agent.mkdir(parents=True)
    for directory in (suite, agent):
        (directory / "__init__.py").write_text("", encoding="utf-8")
    shutil.copyfile(REPO / "glasses3d/tests/agent/conftest.py", agent / "conftest.py")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    paths = [agent / "test_before.py"]
    paths[0].write_text(AGENT_TEST, encoding="utf-8")
    for index in range(interpositions):
        middle, after = suite / f"test_middle_{index}.py", agent / f"test_after_{index}.py"
        middle.write_text(ROOT_TEST, encoding="utf-8")
        after.write_text(AGENT_TEST, encoding="utf-8")
        paths.extend((middle, after))
    env = dict(os.environ, PYTHONPATH=str(REPO), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    for name in ("G3D_OFFLINE", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        env.pop(name, None)
    env.update({name: FAKE_VALUE for name in SCRUBBED})
    result = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-c", str(config), "-p", "no:cacheprovider", "-q",
         *(str(path) for path in paths)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90)
    detail = f"child pytest exit {result.returncode}\n{result.stdout}\n{result.stderr}"
    assert result.returncode == 0, detail
    assert re.search(rf"\b{len(paths)} passed\b", result.stdout), detail
