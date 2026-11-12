import copy
from decimal import Decimal
import pytest
from ledgerly.agent import Agent, ApprovalRequired
from ledgerly.paypal import SandboxMock
from ledgerly.recovery import DurableAgent

SOURCE = "From: Noor Hall <noor@example.test>\n\nField recording — USD 360\nNet 14"
MERCHANT = {"name": "Ledgerly test", "email_address": "merchant@example.test"}

def setup(agent_type=Agent, **kwargs):
    provider = SandboxMock()
    agent = agent_type(provider, MERCHANT, **kwargs)
    result = agent.tool_create_invoice(SOURCE)
    assert result["ok"], result
    invoice = result["invoices"][0]
    return provider, agent, invoice

def correct(agent, identity, rows):
    return agent.prepare_draft_correction(identity, rows, agent.inspect_draft_correction(identity)["revision"])

def items(price="320"):
    return [{"name": "Field recording", "quantity": "1", "unit_price": price}]

def test_same_invoice_retired_old_review_new_separate_send():
    provider, agent, invoice = setup()
    identity = invoice["invoice_id"]
    correction = correct(agent, identity, items())
    assert agent.pending[invoice["approval_id"]].status == "REJECTED"
    assert len(provider.invoices) == 1
    assert agent.ledger[identity].total == Decimal("360")
    with pytest.raises(ValueError):
        agent.approve(invoice["approval_id"])
    result = agent.approve(correction["approval_id"])
    assert result["total"] == "320.00"
    assert len(provider.invoices) == 1
    assert provider.invoices[identity]["status"] == "DRAFT"
    assert agent.pending[result["approval_id"]].status == "PENDING"
    updates = [r for r in provider.requests if r[0] == "PUT"]
    assert len(updates) == 1 and "send_to_recipient=false&send_to_invoicer=false" in updates[0][1]
    assert updates[0][2]["detail"]["invoice_number"] == invoice["invoice_number"]
    assert not [r for r in provider.requests if r[1].endswith("/send")]
    agent.approve(result["approval_id"])
    assert provider.invoices[identity]["status"] == "SENT"

def test_changed_provider_blocks_before_update():
    provider, agent, invoice = setup()
    correction = correct(agent, invoice["invoice_id"], items())
    provider.invoices[invoice["invoice_id"]]["items"][0]["unit_amount"]["value"] = "350"
    provider._recalc(provider.invoices[invoice["invoice_id"]])
    with pytest.raises(ValueError):
        agent.approve(correction["approval_id"])
    assert not [r for r in provider.requests if r[0] == "PUT"]

@pytest.mark.parametrize("price", ["-1", "NaN", "Infinity", "320.001", "0"])
def test_invalid_correction_retains_original_send(price):
    provider, agent, invoice = setup()
    with pytest.raises(ValueError):
        correct(agent, invoice["invoice_id"], items(price))
    assert agent.pending[invoice["approval_id"]].status == "PENDING"
    assert not [r for r in provider.requests if r[0] == "PUT"]

def test_unknown_update_not_replayed_after_restart(tmp_path):
    provider, agent, invoice = setup(DurableAgent, state_file=tmp_path/"state.json")
    correction = correct(agent, invoice["invoice_id"], items())
    actual = provider.update_invoice
    def lost(identity, body):
        actual(identity, body)
        raise TimeoutError("lost response")
    provider.update_invoice = lost
    with pytest.raises(TimeoutError):
        agent.approve(correction["approval_id"])
    restored = DurableAgent(provider, MERCHANT, state_file=tmp_path/"state.json")
    assert restored.pending[correction["approval_id"]].status == "FAILED"
    assert restored.pending[invoice["approval_id"]].status == "REJECTED"
    with pytest.raises(ValueError):
        restored.approve(correction["approval_id"])
    with pytest.raises(ValueError):
        correct(restored, invoice["invoice_id"], items("300"))
    assert len([r for r in provider.requests if r[0] == "PUT"]) == 1

def test_update_cannot_bypass_human_gate():
    provider, agent, invoice = setup()
    with pytest.raises(ApprovalRequired):
        agent.client.update_invoice(invoice["invoice_id"], {})
    assert not [r for r in provider.requests if r[0] == "PUT"]

def test_second_correction_preserves_identity(tmp_path):
    provider, agent, invoice = setup(DurableAgent, state_file=tmp_path/"state.json")
    first = correct(agent, invoice["invoice_id"], items())
    agent.approve(first["approval_id"])
    second = correct(agent, invoice["invoice_id"], items("300"))
    agent.approve(second["approval_id"])
    restored = DurableAgent(provider, MERCHANT, state_file=tmp_path/"state.json")
    assert len(restored.ledger) == 1
    assert restored.ledger[invoice["invoice_id"]].total == Decimal("300")
    assert len(restored.list_pending()) == 1


def test_stale_display_cannot_prepare_over_new_correction():
    provider, agent, invoice = setup()
    identity = invoice["invoice_id"]
    viewed = agent.inspect_draft_correction(identity)
    first = correct(agent, identity, items())
    agent.approve(first["approval_id"])
    with pytest.raises(ValueError, match="displayed draft changed"):
        agent.prepare_draft_correction(identity, items("280"), viewed["revision"])
    assert agent.ledger[identity].total == Decimal("320")
    assert len(agent.list_pending()) == 1

def test_native_correction_confirmation_and_restart(tmp_path):
    from test_invoice_desk import open_desk, create
    provider = SandboxMock()
    path = tmp_path / "native.json"
    desk = open_desk(path, provider)
    assert create(desk)["ok"]
    old = desk.snapshot()["pending"][0]
    identity = old["invoice_id"]
    viewed = desk.dispatch({"action": "inspect_draft_correction", "invoice_id": identity}, "one")
    assert viewed["ok"], viewed
    proposed = [{"name": "Consulting", "quantity": "2", "unit_price": "65"}]
    prepared = desk.dispatch({"action": "prepare_draft_correction", "invoice_id": identity,
                              "revision": viewed["result"]["revision"], "items": proposed}, "one")
    assert prepared["ok"], prepared
    desk = open_desk(path, provider)
    correction = desk.snapshot()["pending"][0]
    assert correction["confirmation"] == "CORRECT " + identity
    denied = desk.dispatch({"action": "approve", "action_id": correction["id"],
                            "confirmation": "CORRECT WRONG"}, "two")
    assert not denied["ok"]
    assert not [r for r in provider.requests if r[0] == "PUT"]
    done = desk.dispatch({"action": "approve", "action_id": correction["id"],
                          "confirmation": correction["confirmation"]}, "two")
    assert done["ok"], done
    desk = open_desk(path, provider)
    state = desk.snapshot()
    assert len(state["ledger"]) == 1 and state["ledger"][0]["total"] == "130.00"
    assert state["ledger"][0]["status"] == "DRAFT"
    assert len(state["pending"]) == 1 and state["pending"][0]["kind"] == "send_invoice"
    assert state["pending"][0]["id"] != old["id"]
    assert state["invoice_details"][0]["record"]["items"][0]["unit_amount"]["value"] == "65"

def test_http_correction_explicitly_suppresses_notifications(monkeypatch):
    from ledgerly.paypal import HttpPayPalClient, Response
    monkeypatch.setenv("LEDGERLY_ALLOW_NETWORK", "1")
    client = HttpPayPalClient("test-id", "test-secret")
    calls = []
    client._call = lambda *args: calls.append(args) or Response(200, {})
    body = {"items": [{"name": "Same draft"}]}
    client.update_invoice("INV2-TEST", body)
    assert calls == [("PUT", "/v2/invoicing/invoices/INV2-TEST?send_to_recipient=false&send_to_invoicer=false", body)]


def test_unit_only_correction_keeps_identity_total_and_separate_send():
    provider, agent, invoice = setup()
    identity = invoice["invoice_id"]
    viewed = agent.inspect_draft_correction(identity)
    assert viewed["items"][0]["unit"] == "QUANTITY"
    correction = agent.prepare_draft_correction(identity,
        [{**items("360")[0], "unit": "HOURS"}], viewed["revision"])
    assert provider.invoices[identity]["items"][0]["unit_of_measure"] == "QUANTITY"
    assert agent.pending[invoice["approval_id"]].status == "REJECTED"
    result = agent.approve(correction["approval_id"])
    assert provider.invoices[identity]["items"][0]["unit_of_measure"] == "HOURS"
    assert provider.invoices[identity]["status"] == "DRAFT"
    assert len(provider.invoices) == 1
    assert agent.ledger[identity].total == Decimal("360")
    assert agent.pending[result["approval_id"]].kind == "send_invoice"
    assert not [r for r in provider.requests if r[1].endswith("/send")]

@pytest.mark.parametrize("unit", ["DAYS", "", [], 1])
def test_unsupported_unit_does_not_retire_current_send_or_update(unit):
    provider, agent, invoice = setup()
    with pytest.raises(ValueError):
        correct(agent, invoice["invoice_id"], [{**items("360")[0], "unit": unit}])
    assert agent.pending[invoice["approval_id"]].status == "PENDING"
    assert not [r for r in provider.requests if r[0] == "PUT"]


def test_add_missing_work_then_remove_it_preserves_invoice_and_separate_approval(tmp_path):
    provider, agent, invoice = setup(DurableAgent, state_file=tmp_path/"state.json")
    identity = invoice["invoice_id"]
    original = {**items("360")[0], "source_index": 0}
    added = {"name": "Delivery", "quantity": "1", "unit_price": "25", "unit": "QUANTITY", "source_index": None}
    proposed = correct(agent, identity, [original, added])
    assert agent.ledger[identity].total == 360
    assert not [r for r in provider.requests if r[0] == "PUT"]
    applied = agent.approve(proposed["approval_id"])
    assert applied["total"] == "385.00"
    restored = DurableAgent(provider, MERCHANT, state_file=tmp_path/"state.json")
    assert len(restored.ledger) == 1 and restored.ledger[identity].total == 385
    assert len(provider.invoices[identity]["items"]) == 2
    remove = correct(restored, identity, [original])
    restored.approve(remove["approval_id"])
    assert restored.ledger[identity].total == 360
    assert len(provider.invoices[identity]["items"]) == 1
    assert provider.invoices[identity]["status"] == "DRAFT"
    assert len(restored.list_pending()) == 1
    assert not [r for r in provider.requests if r[1].endswith("/send")]


@pytest.mark.parametrize("rows", [
    [],
    [{"name":"Work", "quantity":"1", "unit_price":"1", "source_index": True}],
    [{"name":"Work", "quantity":"1", "unit_price":"1", "source_index": 5}],
    [{"name":"Work", "quantity":"1", "unit_price":"1", "source_index": 0}]*2,
    [{"name":"Work", "quantity":"1", "unit_price":"0", "source_index": None}],
])
def test_invalid_scope_change_keeps_original_send_and_provider(rows):
    provider, agent, invoice = setup()
    with pytest.raises(ValueError):
        correct(agent, invoice["invoice_id"], rows)
    assert agent.pending[invoice["approval_id"]].status == "PENDING"
    assert len(provider.invoices[invoice["invoice_id"]]["items"]) == 1
    assert not [r for r in provider.requests if r[0] == "PUT"]


def test_recipient_only_correction_preserves_invoice_and_replaces_send_destination(tmp_path):
    provider, agent, invoice = setup(DurableAgent, state_file=tmp_path/"state.json")
    identity = invoice["invoice_id"]
    viewed = agent.inspect_draft_correction(identity)
    assert viewed["client_email"] == "noor@example.test"
    result = agent.prepare_draft_correction(identity, items("360"), viewed["revision"],
                                            {"name":"Noor Studio", "email":"accounts@example.test"})
    assert agent.ledger[identity].client_email == "noor@example.test"
    assert agent.pending[invoice["approval_id"]].status == "REJECTED"
    assert not [r for r in provider.requests if r[0] == "PUT"]
    proposed = agent.pending[result["approval_id"]]
    assert proposed.payload["invoice"]["primary_recipients"][0]["billing_info"]["email_address"] == "accounts@example.test"
    done = agent.approve(result["approval_id"])
    restored = DurableAgent(provider, MERCHANT, state_file=tmp_path/"state.json")
    assert len(restored.ledger) == 1
    assert restored.ledger[identity].client_email == "accounts@example.test"
    assert restored.ledger[identity].client_name == "Noor Studio"
    assert restored.ledger[identity].total == 360
    assert "accounts@example.test" in restored.pending[done["approval_id"]].summary
    assert provider.invoices[identity]["status"] == "DRAFT"
    assert not [r for r in provider.requests if r[1].endswith("/send")]


@pytest.mark.parametrize("recipient", [
    {"name":"Noor", "email":"not-an-email"},
    {"name":"", "email":"accounts@example.test"},
    {"name":"Noor", "email":"one@example.test, two@example.test"},
    {"name":"Noor", "email":"accounts@example.test", "send":True},
])
def test_invalid_recipient_correction_preserves_original_review(recipient):
    provider, agent, invoice = setup()
    identity = invoice["invoice_id"]
    viewed = agent.inspect_draft_correction(identity)
    with pytest.raises(ValueError):
        agent.prepare_draft_correction(identity, items("360"), viewed["revision"], recipient)
    assert agent.pending[invoice["approval_id"]].status == "PENDING"
    assert not [r for r in provider.requests if r[0] == "PUT"]

@pytest.mark.parametrize('terms,expected', [({'kind':'net','value':'30'},'NET_30'),({'kind':'receipt','value':''},'DUE_ON_RECEIPT'),({'kind':'none','value':''},'NO_DUE_DATE')])
def test_correct_terms_same_invoice_no_send(terms, expected):
    provider, agent, invoice = setup()
    identity = invoice['invoice_id']
    viewed = agent.inspect_draft_correction(identity)
    result = agent.prepare_draft_correction(identity, items('360'), viewed['revision'], terms=terms)
    assert provider.invoices[identity]['detail']['payment_term']['term_type'] != expected
    agent.approve(result['approval_id'])
    assert provider.invoices[identity]['detail']['payment_term']['term_type'] == expected
    assert len(provider.invoices) == 1
    assert agent.ledger[identity].total == Decimal('360')
    assert not [r for r in provider.requests if r[1].endswith('/send')]
    if expected in {'NO_DUE_DATE','DUE_ON_RECEIPT'}:
        assert agent.ledger[identity].due_on is None

@pytest.mark.parametrize('terms', [{'kind':'net','value':'-1'},{'kind':'net','value':'366'},{'kind':'net','value':'1.5'},{'kind':'date','value':'2026-02-30'},{'kind':'date','value':'1900-01-01'},{'kind':'receipt','value':'30'}])
def test_invalid_terms_do_not_retire_send(terms):
    provider, agent, invoice = setup()
    identity=invoice['invoice_id']
    with pytest.raises(ValueError):
        agent.prepare_draft_correction(identity, items('360'), agent.inspect_draft_correction(identity)['revision'], terms=terms)
    assert agent.pending[invoice['approval_id']].status == 'PENDING'
    assert not [r for r in provider.requests if r[0]=='PUT']

def test_note_only_correction_preserves_invoice_and_exact_instructions():
    provider, agent, invoice = setup()
    identity = invoice["invoice_id"]
    original = copy.deepcopy(provider.invoices[identity])
    viewed = agent.inspect_draft_correction(identity)
    note = "Purchase order AW-1042\nProject <North> & café"
    correction = agent.prepare_draft_correction(identity, viewed["items"], viewed["revision"], note=note)
    assert provider.invoices[identity] == original
    queued = agent.pending[correction["approval_id"]]
    assert queued.payload["invoice"]["detail"]["note"] == note
    result = agent.approve(correction["approval_id"])
    updated = provider.invoices[identity]
    assert updated["detail"]["note"] == note
    for key in ("invoice_number", "payment_term", "currency_code"):
        assert updated["detail"][key] == original["detail"][key]
    assert len(updated["items"]) == len(original["items"])
    for after, before in zip(updated["items"], original["items"]):
        assert {k: v for k, v in after.items() if k != "unit_amount"} == {k: v for k, v in before.items() if k != "unit_amount"}
        assert after["unit_amount"]["currency_code"] == before["unit_amount"]["currency_code"]
        assert Decimal(after["unit_amount"]["value"]) == Decimal(before["unit_amount"]["value"])
    assert updated["primary_recipients"] == original["primary_recipients"]
    assert agent.ledger[identity].total == Decimal("360")
    assert updated["status"] == "DRAFT"
    assert agent.pending[result["approval_id"]].payload["invoice"]["detail"]["note"] == note
    assert not [r for r in provider.requests if r[1].endswith("/send")]
    with pytest.raises(ValueError, match="displayed draft changed"):
        agent.prepare_draft_correction(identity, items("360"), viewed["revision"], note="Old edit")

@pytest.mark.parametrize("note", ["", "   \n"])
def test_clear_optional_note_omits_provider_field(note):
    provider, agent, invoice = setup()
    identity = invoice["invoice_id"]
    viewed = agent.inspect_draft_correction(identity)
    correction = agent.prepare_draft_correction(identity, viewed["items"], viewed["revision"], note=note)
    agent.approve(correction["approval_id"])
    assert "note" not in provider.invoices[identity]["detail"]
    assert agent.inspect_draft_correction(identity)["note"] == ""

@pytest.mark.parametrize("note", ["x" * 4001, 7, {"note": "PO"}])
def test_invalid_note_keeps_original_send_review(note):
    provider, agent, invoice = setup()
    identity = invoice["invoice_id"]
    viewed = agent.inspect_draft_correction(identity)
    with pytest.raises(ValueError, match="4000"):
        agent.prepare_draft_correction(identity, viewed["items"], viewed["revision"], note=note)
    assert agent.pending[invoice["approval_id"]].status == "PENDING"
    assert not [r for r in provider.requests if r[0] == "PUT"]

def test_maximum_note_length_is_accepted():
    provider, agent, invoice = setup()
    identity = invoice["invoice_id"]
    viewed = agent.inspect_draft_correction(identity)
    correction = agent.prepare_draft_correction(identity, items("360"), viewed["revision"], note="é" * 4000)
    assert agent.pending[correction["approval_id"]].payload["invoice"]["detail"]["note"] == "é" * 4000
