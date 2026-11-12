from datetime import date
from decimal import Decimal
import pytest
from ledgerly.extract import RulesExtractor
from ledgerly.paypal import build_invoice, retained_unit_label, SandboxMock
from ledgerly.agent import Agent

MERCHANT={"name":"Morrow Studio","email_address":"studio@example.test"}
SOURCE="From: Northbank <accounts@northbank.example>\n3 x USD 145 programme revisions\nNet 14"

def extraction(unit):
    ex=RulesExtractor().extract(SOURCE)
    assert len(ex.line_items)==1
    ex.line_items[0].unit=unit
    return ex

@pytest.mark.parametrize("unit",["sessions","days","workshops","headshots","箱"])
def test_custom_count_is_retained_in_supported_description(unit):
    body=build_invoice(extraction(unit),MERCHANT,"TEST-1",date(2026,11,11))
    item=body["items"][0]
    assert item["unit_of_measure"]=="QUANTITY"
    assert item["description"]=="Unit: "+unit
    assert retained_unit_label(item)==unit
    assert item["quantity"]=="3" and item["unit_amount"]["value"]=="145.00"

@pytest.mark.parametrize("unit",["h","hr","hrs","hour","hours","HOURS"])
def test_only_hour_aliases_use_hours(unit):
    item=build_invoice(extraction(unit),MERCHANT,"T",date(2026,11,11))["items"][0]
    assert item["unit_of_measure"]=="HOURS" and "description" not in item

def test_retained_unit_description_is_literal_and_bounded():
    assert retained_unit_label({"unit_of_measure":"QUANTITY","description":"Unit: sessions"})=="sessions"
    for value in ["Unit: ","Unit: sessions\nOther terms","Unrelated description","Unit: "+"x"*995]:
        assert retained_unit_label({"unit_of_measure":"QUANTITY","description":value}) is None
    assert retained_unit_label({"unit_of_measure":"HOURS","description":"Unit: sessions"}) is None
    with pytest.raises(ValueError,match="unit"):
        build_invoice(extraction("x"*995),MERCHANT,"T",date(2026,11,11))

def test_correction_retains_count_label_until_unit_is_explicitly_changed():
    class Extractor:
        def extract(self,text):return extraction("sessions")
    provider=SandboxMock()
    agent=Agent(provider,MERCHANT,extractor=Extractor())
    created=agent.tool_create_invoice(SOURCE)["invoices"][0]
    identity=created["invoice_id"]
    viewed=agent.inspect_draft_correction(identity)
    edited=[{"name":"Programme revisions","quantity":"2","unit_price":"145","unit":"QUANTITY"}]
    pending=agent.prepare_draft_correction(identity,edited,viewed["revision"])
    agent.approve(pending["approval_id"])
    assert provider.invoices[identity]["items"][0]["description"]=="Unit: sessions"
    assert agent.ledger[identity].total==Decimal("290")
    viewed=agent.inspect_draft_correction(identity)
    edited[0]["unit"]="HOURS"
    pending=agent.prepare_draft_correction(identity,edited,viewed["revision"])
    agent.approve(pending["approval_id"])
    assert "description" not in provider.invoices[identity]["items"][0]
    assert provider.invoices[identity]["items"][0]["unit_of_measure"]=="HOURS"
    assert not [r for r in provider.requests if r[1].endswith("/send")]
