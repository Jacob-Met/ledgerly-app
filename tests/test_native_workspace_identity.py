"""Recovery identity is stable across process sessions, never an access token."""
import json
import re
import threading
from urllib.request import urlopen
from pathlib import Path

from ledgerly.extract import RulesExtractor
from ledgerly.invoice_desk import NativeSession, load_browser_bridge, server
from ledgerly.paypal import SandboxMock
from ledgerly.recovery import DurableAgent

ROOT = Path(__file__).resolve().parents[1]

def desk(path, email="studio@example.test", provider="Controlled mock provider"):
    client = SandboxMock()
    agent = DurableAgent(client, {"name": "Studio", "email_address": email},
                         extractor=RulesExtractor(), state_file=path)
    return NativeSession(agent, load_browser_bridge(ROOT / "web-demo/python"), provider=provider), client

def test_workspace_identity_survives_restart_and_canonical_path(tmp_path, monkeypatch):
    path = tmp_path / "private-ledger.json"
    first, client = desk(path)
    monkeypatch.chdir(tmp_path)
    second, other = desk(Path(".") / path.name)
    assert first.workspace_id == second.workspace_id
    assert re.fullmatch(r"[0-9a-f]{64}", first.workspace_id)
    assert not path.exists()  # No ledger or metadata writes just to identify it.
    assert not client.requests and not other.requests

def test_workspace_identity_separates_state_account_and_provider(tmp_path):
    path = tmp_path / "ledger.json"
    original, _ = desk(path)
    moved, _ = desk(tmp_path / "another-ledger.json")
    account, _ = desk(path, email="other@example.test")
    provider, _ = desk(path, provider="Another controlled provider")
    assert len({original.workspace_id, moved.workspace_id, account.workspace_id, provider.workspace_id}) == 4

def test_bootstrap_identity_stays_stable_while_access_token_rotates(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html><head></head><body>Desk</body></html>")
    configs = []
    for _ in range(2):
        session, client = desk(tmp_path / "ledger.json")
        http = server(session, web)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{http.server_port}/", timeout=3) as reply:
                html = reply.read().decode()
            config = json.loads(re.search(r'<script id="ledgerly-native" type="application/json">(.*?)</script>', html).group(1))
            assert config["workspace_id"] == session.workspace_id
            assert config["workspace_id"] != config["token"]
            assert str(tmp_path) not in html and "studio@example.test" not in html
            configs.append(config)
            assert not client.requests
        finally:
            http.shutdown()
            http.server_close()
            thread.join(timeout=3)
    assert configs[0]["workspace_id"] == configs[1]["workspace_id"]
    assert configs[0]["token"] != configs[1]["token"]
