import json
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from ledgerly.retainer import RetainerDesk, RetainerError, SandboxRetainers, payload_for
from ledgerly.paypal import PayPalRequestNotSent, SANDBOX_BASE
from ledgerly.retainer_web import server

def data(**kw):
    return dict(customer="North Studio",email="buyer@example.test",scope="Two edited interviews monthly; one revision each.",
                source="Agreement: USD 450 per month for two interviews, 180 minutes estimated delivery.",monthly_usd="450.00",minutes=180,capacity=400,**kw)
class Gateway:
    def __init__(self):self.calls=[];self.status="APPROVAL_PENDING";self.row=None;self.fail=None
    def create(self,kind,payload,request_id):
        self.calls.append((kind,payload,request_id))
        if self.fail:raise self.fail
        if kind=="subscription":
            self.row={"id":"I-TEST123","plan_id":payload["plan_id"],"custom_id":payload["custom_id"]}
            return dict(self.row,status="APPROVAL_PENDING",links=[{"rel":"approve","href":"https://www.sandbox.paypal.com/webapps/billing/subscriptions?ba_token=TEST"}])
        return {"id":{"product":"PROD-TEST123","plan":"P-TEST123"}[kind]}
    def subscription(self,key):return dict(self.row,status=self.status)

@pytest.fixture
def setup(tmp_path):
    g=Gateway();desk=RetainerDesk(tmp_path/"desk.sqlite",lambda:g)
    return desk,g
def reviewed(desk):
    r=desk.save_draft(data())
    return desk.approve(r["id"],r["review_hash"])
def create(desk,r,kind):
    return desk.create_resource(r["id"],kind,f"CREATE {kind.upper()} {r['id'][:8]}","http://127.0.0.1:8768/?returned=1")

def test_complete_persisted_subscription_review(setup):
    desk,g=setup;r=reviewed(desk)
    for kind in ("product","plan","subscription"):r=create(desk,r,kind)
    restored=RetainerDesk(desk.path,lambda:g)
    assert restored.snapshot()["reserved_minutes"]==180
    assert restored.snapshot()["offers"][0]["state"]=="AWAITING_BUYER"
    g.status="ACTIVE"
    r=restored.refresh(r["id"])
    assert r["provider_status"]=="ACTIVE"
    assert "paid" not in r
    assert len(g.calls)==3
    assert g.calls[1][1]["billing_cycles"][0]["pricing_scheme"]["fixed_price"]=={"value":"450.00","currency_code":"USD"}

def test_capacity_reservation_atomic_between_connections(setup):
    desk,g=setup; a=desk.save_draft(data());b=desk.save_draft({**data(),"minutes":300})
    def approve(r):
        try:RetainerDesk(desk.path).approve(r["id"],r["review_hash"]);return True
        except RetainerError:return False
    with ThreadPoolExecutor() as pool:results=list(pool.map(approve,[a,b]))
    assert sorted(results)==[False,True]
    assert desk.snapshot()["reserved_minutes"] in (180,300)

def test_changed_terms_cannot_use_old_review(setup):
    desk,g=setup;r=desk.save_draft(data());old=r["review_hash"]
    r=desk.save_draft({**r,"monthly_usd":"950"})
    with pytest.raises(RetainerError):desk.approve(r["id"],old)
    assert desk.snapshot()["reserved_minutes"]==0

@pytest.mark.parametrize("value",["NaN","Infinity","0","-1","3.001","1000001"])
def test_invalid_prices(setup,value):
    with pytest.raises(RetainerError):setup[0].save_draft({**data(),"monthly_usd":value})

def test_one_send_under_concurrent_clicks(setup):
    desk,g=setup;r=reviewed(desk)
    def attempt(_):
        try:create(RetainerDesk(desk.path,lambda:g),r,"product");return True
        except RetainerError:return False
    with ThreadPoolExecutor() as pool:results=list(pool.map(attempt,range(4)))
    assert results.count(True)==1
    assert len(g.calls)==1

def test_network_unknown_survives_restart_and_never_replays(setup):
    desk,g=setup;r=reviewed(desk);g.fail=TimeoutError()
    with pytest.raises(RetainerError):create(desk,r,"product")
    restored=RetainerDesk(desk.path,lambda:g)
    assert restored.snapshot()["offers"][0]["state"]=="PRODUCT_UNKNOWN"
    with pytest.raises(RetainerError):create(restored,r,"product")
    assert len(g.calls)==1
    with pytest.raises(RetainerError):restored.archive(r["id"])

def test_auth_failure_restores_ready_state_same_request_id(setup):
    desk,g=setup;r=reviewed(desk);g.fail=PayPalRequestNotSent()
    with pytest.raises(RetainerError):create(desk,r,"product")
    assert desk.snapshot()["offers"][0]["state"]=="REVIEWED"
    g.fail=None;create(desk,r,"product")
    assert g.calls[0][2]==g.calls[1][2]

def test_missing_config_does_not_reserve_operation(setup):
    desk,g=setup;r=reviewed(desk)
    def fail():raise RetainerError("No credentials")
    desk.gateway_factory=fail
    with pytest.raises(RetainerError):create(desk,r,"product")
    assert desk.snapshot()["offers"][0]["operations"]==[]

def test_foreign_subscription_status_is_not_adopted(setup):
    desk,g=setup;r=reviewed(desk)
    for kind in ("product","plan","subscription"):r=create(desk,r,kind)
    g.row["plan_id"]="P-OTHER"
    with pytest.raises(RetainerError):desk.refresh(r["id"])
    assert desk.snapshot()["offers"][0]["provider_status"]=="APPROVAL_PENDING"

def test_archive_releases_only_unsent_reservation(setup):
    desk,g=setup;r=reviewed(desk);desk.archive(r["id"])
    assert desk.snapshot()["reserved_minutes"]==0

def test_sandbox_only_transport():
    class Client:base="https://api-m.paypal.com"
    with pytest.raises(RetainerError):SandboxRetainers(Client())

def test_real_http_draft_review_restart(setup):
    desk,g=setup
    http=server(desk);thread=threading.Thread(target=http.serve_forever, kwargs={"poll_interval": 0.01},daemon=True);thread.start()
    base=f"http://127.0.0.1:{http.server_port}"
    def request(path,data=None,token=True):
        req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data else None,
            headers={"Content-Type":"application/json",**({"X-Ledgerly-Token":http.token} if token else {})})
        return json.load(urllib.request.urlopen(req))
    try:
        html=urllib.request.urlopen(base).read().decode()
        assert "Monthly commitments" in html
        r=request("/api/draft",data())
        request("/api/approve",{"id":r["id"],"review_hash":r["review_hash"]})
        assert request("/api/desk")["reserved_minutes"]==180
        with pytest.raises(urllib.error.HTTPError):request("/api/draft",data(),False)
    finally:http.shutdown();http.server_close()

def test_existing_operator_opens_retainer_with_imported_email(tmp_path,monkeypatch):
    from ledgerly.operator import main
    import ledgerly.retainer_web
    source=tmp_path/"agreement.eml"
    source.write_text("From: Buyer <buyer@example.test>\nTo: seller@example.test\nSubject: Monthly work\nContent-Type: text/plain\n\nUSD 450 monthly for two interviews.")
    captured=[]
    monkeypatch.setattr(ledgerly.retainer_web,"serve",lambda desk,port:captured.append(desk.snapshot()))
    assert main([str(source),"--retainers","--retainer-store",str(tmp_path/"desk.sqlite")])==0
    assert "USD 450 monthly" in captured[0]["initial_source"]
    assert captured[0]["offers"]==[]

def test_sandbox_adapter_over_actual_synthetic_http(tmp_path,monkeypatch):
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from ledgerly.paypal import HttpPayPalClient
    calls=[]
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_POST(self):
            body=self.rfile.read(int(self.headers.get("Content-Length","0")))
            calls.append((self.path,body,self.headers.get("PayPal-Request-Id")))
            if self.path=="/v1/oauth2/token":result={"access_token":"fixture-token","expires_in":3600}
            elif self.path=="/v1/catalogs/products":result={"id":"PROD-HTTP"}
            elif self.path=="/v1/billing/plans":result={"id":"P-HTTP"}
            else:
                payload=json.loads(body)
                result={"id":"I-HTTP","status":"APPROVAL_PENDING","plan_id":payload["plan_id"],
                    "custom_id":payload["custom_id"],"links":[{"rel":"approve","href":"https://www.sandbox.paypal.com/approve?fixture=1"}]}
            raw=json.dumps(result).encode()
            self.send_response(201);self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw)
    provider=ThreadingHTTPServer(("127.0.0.1",0),Provider)
    t=threading.Thread(target=provider.serve_forever, kwargs={"poll_interval": 0.01},daemon=True);t.start()
    original=urllib.request.urlopen
    def transport(req,*args,**kw):
        assert req.full_url.startswith(SANDBOX_BASE)
        local=urllib.request.Request(req.full_url.replace(SANDBOX_BASE,f"http://127.0.0.1:{provider.server_port}"),
            data=req.data,headers=dict(req.header_items()),method=req.method)
        return original(local,*args,**kw)
    monkeypatch.setenv("LEDGERLY_ALLOW_NETWORK","1")
    monkeypatch.setattr(urllib.request,"urlopen",transport)
    gateway=SandboxRetainers(HttpPayPalClient("fixture","fixture",SANDBOX_BASE))
    try:
        desk=RetainerDesk(tmp_path/"http.sqlite",lambda:gateway)
        r=reviewed(desk)
        for kind in ("product","plan","subscription"):r=create(desk,r,kind)
        assert [c[0] for c in calls]==["/v1/oauth2/token","/v1/catalogs/products","/v1/billing/plans","/v1/billing/subscriptions"]
        assert all(c[2] for c in calls[1:])
        assert len(set(c[2] for c in calls[1:]))==3
        assert r["state"]=="AWAITING_BUYER"
    finally:provider.shutdown();provider.server_close()

def test_capacity_release_requires_fresh_terminal_provider_state(setup):
    desk,g=setup;r=reviewed(desk)
    for kind in ("product","plan","subscription"):r=create(desk,r,kind)
    g.status="ACTIVE"
    with pytest.raises(RetainerError):desk.archive(r["id"])
    assert desk.snapshot()["reserved_minutes"]==180
    g.status="CANCELLED";desk.archive(r["id"])
    assert desk.snapshot()["reserved_minutes"]==0
    assert desk.snapshot()["offers"][0]["provider_status"]=="CANCELLED"
