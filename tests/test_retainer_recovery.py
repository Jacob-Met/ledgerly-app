"""Recovery uses authored provider responses, never real PayPal success evidence."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from ledgerly.retainer import RetainerDesk, RetainerError, SandboxRetainers
from ledgerly.paypal import SANDBOX_BASE
from test_retainer import data, reviewed, create

class LostReplyGateway:
    def __init__(self):
        self.objects={};self.posts=[];self.reads=[];self.lose=True;self.read_error=None
    def create(self,kind,payload,request_id):
        identifier={"product":"PROD-RECOVERY","plan":"P-RECOVERY","subscription":"I-RECOVERY"}[kind]
        result={**copy.deepcopy(payload),"id":identifier}
        if kind=="subscription":
            result["status"]="APPROVAL_PENDING"
            result["links"]=[{"rel":"approve","href":"https://www.sandbox.paypal.com/approve?fixture=1"}]
        self.objects[kind,identifier]=result;self.posts.append((kind,request_id))
        if self.lose:raise TimeoutError("Response lost after provider acceptance")
        return result
    def resource(self,kind,identifier):
        self.reads.append((kind,identifier))
        if self.read_error:raise self.read_error
        return copy.deepcopy(self.objects[kind,identifier])
    def subscription(self,identifier):return self.resource("subscription",identifier)

@pytest.fixture
def setup(tmp_path):
    gateway=LostReplyGateway()
    return RetainerDesk(tmp_path/"recovery.sqlite",lambda:gateway),gateway

def lost(desk,gateway,kind):
    r=reviewed(desk);gateway.lose=False
    for stage in ("product","plan","subscription"):
        if stage==kind:break
        r=create(desk,r,stage)
    gateway.lose=True
    with pytest.raises(RetainerError,match="uncertain"):create(desk,r,kind)
    return r,{"product":"PROD-RECOVERY","plan":"P-RECOVERY","subscription":"I-RECOVERY"}[kind]

@pytest.mark.parametrize("kind",["product","plan","subscription"])
def test_lost_response_recovered_after_restart_without_post(setup,kind):
    desk,g=setup;r,identifier=lost(desk,g,kind);count=len(g.posts)
    restored=RetainerDesk(desk.path,lambda:g)
    result=restored.reconcile(r["id"],kind,identifier)
    assert result[kind+"_id"]==identifier and result["recovered_at"]
    assert len(g.posts)==count
    assert restored.snapshot()["reserved_minutes"]==180
    assert all(x["state"]=="CONFIRMED" for x in restored.snapshot()["offers"][0]["operations"])
    with pytest.raises(RetainerError):restored.reconcile(r["id"],kind,identifier)
    assert len(g.posts)==count

@pytest.mark.parametrize("fault",["foreign_id","foreign_marker","missing_marker","wrong_name","get_failure"])
def test_product_rejects_nonmatching_evidence_without_release_or_replay(setup,fault):
    desk,g=setup;r,identifier=lost(desk,g,"product");obj=g.objects["product",identifier]
    if fault=="foreign_id":obj["id"]="PROD-OTHER"
    elif fault=="foreign_marker":obj["description"]=obj["description"].replace(r["id"],"0"*32)
    elif fault=="missing_marker":obj.pop("description")
    elif fault=="wrong_name":obj["name"]="Somebody else"
    else:g.read_error=TimeoutError()
    with pytest.raises(RetainerError):desk.reconcile(r["id"],"product",identifier)
    assert desk.snapshot()["offers"][0]["state"]=="PRODUCT_UNKNOWN"
    assert desk.snapshot()["reserved_minutes"]==180 and len(g.posts)==1

@pytest.mark.parametrize("fault",["price","currency","fee","tax","trial","product","quantity","tier"])
def test_plan_recovery_rejects_changed_financial_terms(setup,fault):
    desk,g=setup;r,identifier=lost(desk,g,"plan");obj=g.objects["plan",identifier]
    if fault=="price":obj["billing_cycles"][0]["pricing_scheme"]["fixed_price"]["value"]="451"
    elif fault=="currency":obj["billing_cycles"][0]["pricing_scheme"]["fixed_price"]["currency_code"]="EUR"
    elif fault=="fee":obj["payment_preferences"]["setup_fee"]={"value":"20","currency_code":"USD"}
    elif fault=="tax":obj["taxes"]={"percentage":"10"}
    elif fault=="trial":obj["billing_cycles"].append(copy.deepcopy(obj["billing_cycles"][0]))
    elif fault=="product":obj["product_id"]="PROD-OTHER"
    elif fault=="quantity":obj["quantity_supported"]=True
    else:obj["billing_cycles"][0]["pricing_scheme"]["pricing_model"]="VOLUME"
    with pytest.raises(RetainerError):desk.reconcile(r["id"],"plan",identifier)
    assert desk.snapshot()["offers"][0]["state"]=="PLAN_UNKNOWN"
    assert len(g.posts)==2

@pytest.mark.parametrize("fault",["custom_id","plan_id","override","quantity","changed_plan","embedded_plan"])
def test_subscription_recovery_rejects_identity_or_price_drift(setup,fault):
    desk,g=setup;r,identifier=lost(desk,g,"subscription");obj=g.objects["subscription",identifier]
    if fault=="changed_plan":g.objects["plan","P-RECOVERY"]["billing_cycles"][0]["pricing_scheme"]["fixed_price"]["value"]="999"
    elif fault=="override":obj["plan_overridden"]=True
    elif fault=="embedded_plan":obj["plan"]={"billing_cycles":[]}
    elif fault=="quantity":obj["quantity"]="2"
    else:obj[fault]="foreign-value"
    with pytest.raises(RetainerError):desk.reconcile(r["id"],"subscription",identifier)
    assert desk.snapshot()["offers"][0]["state"]=="SUBSCRIPTION_UNKNOWN"
    assert len(g.posts)==3

def test_terminal_subscription_recovery_can_release_after_fresh_confirmation(setup):
    desk,g=setup;r,identifier=lost(desk,g,"subscription")
    g.objects["subscription",identifier]["status"]="CANCELLED"
    g.objects["plan","P-RECOVERY"]["status"]="INACTIVE"
    desk.reconcile(r["id"],"subscription",identifier)
    desk.archive(r["id"])
    assert desk.snapshot()["reserved_minutes"]==0 and len(g.posts)==3

def test_missing_approval_link_never_invents_one(setup):
    desk,g=setup;r,identifier=lost(desk,g,"subscription")
    g.objects["subscription",identifier]["links"]=[]
    recovered=desk.reconcile(r["id"],"subscription",identifier)
    assert recovered["provider_status"]=="APPROVAL_PENDING" and "approval_url" not in recovered

def test_old_unmarked_request_remains_unknown_without_provider_read(setup):
    desk,g=setup;r,identifier=lost(desk,g,"product")
    with desk.connect() as db:
        payload=json.loads(db.execute("SELECT payload FROM operations").fetchone()[0])
        payload["description"]="Old offer without identity"
        db.execute("UPDATE operations SET payload=?",(json.dumps(payload),))
    with pytest.raises(RetainerError,match="older request"):desk.reconcile(r["id"],"product",identifier)
    assert g.reads==[]

def test_two_recovery_clicks_cannot_advance_same_journal_twice(setup):
    desk,g=setup;r,identifier=lost(desk,g,"product")
    def attempt(_):
        try:RetainerDesk(desk.path,lambda:g).reconcile(r["id"],"product",identifier);return True
        except RetainerError:return False
    with ThreadPoolExecutor() as pool:results=list(pool.map(attempt,range(2)))
    assert results.count(True)==1 and len(g.posts)==1

def test_adapter_only_gets_requested_valid_ids():
    class Response:
        body={"id":"PROD-RECOVERY"}
    class Client:
        base=SANDBOX_BASE
        def __init__(self):self.calls=[]
        def _call(self,*args):self.calls.append(args);return Response()
    c=Client();g=SandboxRetainers(c)
    for kind in ("product","plan","subscription"):g.resource(kind,"SAFE-ID")
    assert all(x[0]=="GET" for x in c.calls)
    with pytest.raises(RetainerError):g.resource("product","../wrong")
    assert len(c.calls)==3
