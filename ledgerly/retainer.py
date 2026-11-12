"""Persistent, human-reviewed monthly retainers; PayPal Sandbox only.

No automatic collection, activation, cancellation, retries, or model decisions.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from .paypal import HttpPayPalClient, SANDBOX_BASE, PayPalRequestNotSent

class RetainerError(ValueError):
    pass

def amount(value):
    try:
        d = Decimal(str(value))
        if not d.is_finite() or d <= 0 or d > 1000000 or d != d.quantize(Decimal(".01")):
            raise ValueError()
        return format(d, ".2f")
    except (InvalidOperation, ValueError):
        raise RetainerError("Monthly USD amount must be positive with at most two decimals.") from None

def text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise RetainerError(f"{name} is required (maximum {limit} characters).")
    return value.strip()

def integer(value, name, maximum=100000):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise RetainerError(f"{name} must be a whole number.")
    result = int(value)
    if result > maximum:
        raise RetainerError(f"{name} exceeds {maximum}.")
    return result

def offer_input(data):
    customer = text(data.get("customer"), "Customer", 100)
    email = text(data.get("email"), "Buyer email", 254)
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise RetainerError("Enter a buyer email.")
    scope = text(data.get("scope"), "Monthly deliverables", 1000)
    source = text(data.get("source"), "Agreement/source text", 12000)
    minutes = integer(data.get("minutes"), "Monthly delivery minutes")
    capacity = integer(data.get("capacity"), "Total monthly capacity")
    if minutes <= 0 or capacity <= 0:
        raise RetainerError("Delivery minutes and capacity must be positive.")
    return dict(customer=customer, email=email, scope=scope, source=source,
                monthly_usd=amount(data.get("monthly_usd")), minutes=minutes, capacity=capacity)

def now():
    return datetime.now(timezone.utc).isoformat()

class SandboxRetainers:
    def __init__(self, client):
        if client.base.rstrip("/") != SANDBOX_BASE:
            raise RetainerError("Retainers only support PayPal Sandbox.")
        self.client = client

    def create(self, kind, payload, request_id):
        path = {"product":"/v1/catalogs/products", "plan":"/v1/billing/plans",
                "subscription":"/v1/billing/subscriptions"}[kind]
        return self.client._call("POST", path, payload,
            {"PayPal-Request-Id":request_id, "Prefer":"return=representation"},
            classify_unsent_auth=True).body

    def resource(self, kind, identifier):
        if kind not in ("product", "plan", "subscription"):
            raise RetainerError("Unknown resource.")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9-]{3,80}", identifier):
            raise RetainerError("Invalid resource identifier.")
        path = {"product":"/v1/catalogs/products/", "plan":"/v1/billing/plans/",
                "subscription":"/v1/billing/subscriptions/"}[kind]
        return self.client._call("GET", path + identifier).body

    def subscription(self, identifier):
        if not re.fullmatch(r"[A-Za-z0-9-]{3,80}", identifier):
            raise RetainerError("Invalid subscription identifier.")
        return self.client._call("GET", "/v1/billing/subscriptions/" + identifier).body

def real_gateway():
    cid, secret = os.environ.get("PAYPAL_CLIENT_ID"), os.environ.get("PAYPAL_CLIENT_SECRET")
    if not cid or not secret:
        raise RetainerError("Configure PayPal Sandbox credentials before creating provider resources.")
    return SandboxRetainers(HttpPayPalClient(cid, secret, SANDBOX_BASE))

def marked_description(row, limit):
    marker = " [Ledgerly offer: " + row["id"] + "]"
    return row["scope"][:limit-len(marker)] + marker

def payload_for(row, kind, return_url):
    if kind == "product":
        return {"name": "Ledgerly / " + row["customer"], "description":marked_description(row,256), "type":"SERVICE"}
    if kind == "plan":
        return {"product_id":row["product_id"], "name":"Monthly / " + row["customer"],
                "description":marked_description(row,127), "status":"ACTIVE",
                "billing_cycles":[{"frequency":{"interval_unit":"MONTH","interval_count":1},
                    "tenure_type":"REGULAR","sequence":1,"total_cycles":0,
                    "pricing_scheme":{"fixed_price":{"value":row["monthly_usd"],"currency_code":"USD"}}}],
                "payment_preferences":{"auto_bill_outstanding":False,"payment_failure_threshold":1}}
    return {"plan_id":row["plan_id"], "custom_id":row["id"],
            "subscriber":{"email_address":row["email"]},
            "application_context":{"brand_name":"Ledgerly", "shipping_preference":"NO_SHIPPING",
                "user_action":"SUBSCRIBE_NOW", "return_url":return_url, "cancel_url":return_url}}


def matches_payload(expected, actual):
    """Compare submitted terms while allowing provider-added response metadata."""
    if isinstance(expected,dict):
        return isinstance(actual,dict) and all(k in actual and matches_payload(v,actual[k]) for k,v in expected.items())
    if isinstance(expected,list):
        return isinstance(actual,list) and len(expected)==len(actual) and all(matches_payload(a,b) for a,b in zip(expected,actual))
    if isinstance(expected,bool):
        return type(actual) is bool and expected == actual
    if isinstance(expected,str) and re.fullmatch(r"[0-9]+\.[0-9]{2}",expected):
        try:
            return Decimal(expected) == Decimal(str(actual))
        except InvalidOperation:
            return False
    return type(expected) is type(actual) and expected == actual

def validate_plan(plan):
    """Reject price additions that were not included in the submitted offer."""
    preferences = plan.get("payment_preferences",{})
    try:
        fee = preferences.get("setup_fee",{})
        if Decimal(str(fee.get("value","0"))) != 0 or Decimal(str(plan.get("taxes",{}).get("percentage","0"))) != 0:
            raise RetainerError("Provider plan adds an unreviewed setup fee or tax.")
        if plan.get("quantity_supported"):
            raise RetainerError("Provider plan permits unreviewed quantities.")
        for cycle in plan.get("billing_cycles",[]):
            pricing = cycle.get("pricing_scheme",{})
            if pricing.get("pricing_model") or pricing.get("tiers"):
                raise RetainerError("Provider plan uses unreviewed tiered pricing.")
    except (InvalidOperation,AttributeError,TypeError):
        raise RetainerError("Provider plan terms are incomplete or malformed.") from None

class RetainerDesk:
    def __init__(self, path, gateway_factory=real_gateway):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.gateway_factory = gateway_factory
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS offers(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY, offer_id TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operations(
              offer_id TEXT, kind TEXT, request_id TEXT UNIQUE, state TEXT,
              payload TEXT, result TEXT, PRIMARY KEY(offer_id,kind));
            """)
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _read(self, db, key):
        row = db.execute("SELECT data FROM offers WHERE id=?", (key,)).fetchone()
        if not row:
            raise RetainerError("Retainer not found.")
        return json.loads(row[0])

    def _save(self, db, row):
        row["updated_at"] = now()
        db.execute("INSERT OR REPLACE INTO offers VALUES (?,?)", (row["id"],json.dumps(row)))

    def _rows(self, db):
        return [json.loads(x[0]) for x in db.execute("SELECT data FROM offers ORDER BY rowid DESC")]

    def snapshot(self):
        with self.connect() as db:
            rows = self._rows(db)
            deliveries = [json.loads(x[0]) for x in db.execute("SELECT data FROM deliveries ORDER BY id")]
            for row in rows:
                row["operations"] = [dict(zip(("kind","request_id","state"),op))
                    for op in db.execute("SELECT kind,request_id,state FROM operations WHERE offer_id=?", (row["id"],))]
        reserved = sum(x["minutes"] for x in rows if x["state"] not in ("DRAFT","ARCHIVED"))
        return {"offers":rows, "deliveries":deliveries, "initial_source":getattr(self,"initial_source",""), "reserved_minutes":reserved, "environment":"PayPal Sandbox",
                "network_enabled":os.environ.get("LEDGERLY_ALLOW_NETWORK") == "1",
                "credentials_configured":bool(os.environ.get("PAYPAL_CLIENT_ID") and os.environ.get("PAYPAL_CLIENT_SECRET"))}

    def save_delivery(self, data):
        """Save an explicit local work item, never a provider or capacity effect."""
        from datetime import date
        key = data.get("id")
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9-]{16,64}", key):
            raise RetainerError("A stable delivery reference is required.")
        title = data.get("title")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 300:
            raise RetainerError("Describe the delivery in 1–300 characters.")
        due = data.get("due_on")
        try:
            if not isinstance(due, str) or date.fromisoformat(due).isoformat() != due:
                raise ValueError()
        except ValueError:
            raise RetainerError("Choose a valid delivery date (YYYY-MM-DD).") from None
        minutes = data.get("minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 100000:
            raise RetainerError("Delivery estimate must be 1–100000 whole minutes.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            offer = self._read(db, data.get("offer_id"))
            if offer["state"] in ("DRAFT", "ARCHIVED"):
                raise RetainerError("Plan deliveries for a reviewed, unarchived retainer.")
            old = db.execute("SELECT data FROM deliveries WHERE id=?", (key,)).fetchone()
            fields = {"offer_id":offer["id"], "title":title.strip(), "due_on":due, "minutes":minutes}
            if old:
                row = json.loads(old[0])
                if row["offer_id"] != offer["id"]:
                    raise RetainerError("A delivery cannot move to another retainer.")
                if all(row[k] == v for k,v in fields.items()):
                    return row
                if row["state"] != "PLANNED" or row["revision"] != data.get("revision"):
                    raise RetainerError("Delivery changed or is complete. Reload before editing.")
                row["revision"] += 1
            else:
                if data.get("revision") is not None:
                    raise RetainerError("Delivery not found. Reload the worklist.")
                row = {"id":key,"state":"PLANNED","revision":1,"created_at":now(),"history":[]}
            row.update(fields)
            row["updated_at"] = now()
            db.execute("INSERT OR REPLACE INTO deliveries VALUES (?,?,?)", (key,offer["id"],json.dumps(row)))
        return row

    def set_delivery_state(self, key, state, revision):
        if state not in ("PLANNED", "COMPLETE", "DROPPED"):
            raise RetainerError("Choose planned, complete or dropped.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT data FROM deliveries WHERE id=?", (key,)).fetchone()
            if not old:
                raise RetainerError("Delivery not found.")
            row = json.loads(old[0])
            if row["state"] == state:
                return row
            if row["revision"] != revision:
                raise RetainerError("Delivery changed. Reload before changing its status.")
            row["state"] = state
            row["revision"] += 1
            row["updated_at"] = now()
            row["history"].append({"state":state,"at":row["updated_at"]})
            db.execute("UPDATE deliveries SET data=? WHERE id=?", (json.dumps(row),key))
        return row

    def save_draft(self, data):
        fields = offer_input(data)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if data.get("id"):
                row = self._read(db, data["id"])
                if row["state"] != "DRAFT":
                    raise RetainerError("Reviewed terms are immutable; create a separate draft for a new offer.")
            else:
                row = {"id":secrets.token_hex(16),"state":"DRAFT","created_at":now()}
            row.update(fields)
            row["review_hash"] = hashlib.sha256(json.dumps(fields,sort_keys=True).encode()).hexdigest()
            self._save(db,row)
        return row

    def approve(self, key, review_hash):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._read(db,key)
            if row["state"] != "DRAFT" or row["review_hash"] != review_hash:
                raise RetainerError("Terms changed or were already reviewed. Reload and review this exact offer.")
            committed = sum(r["minutes"] for r in self._rows(db) if r["state"] not in ("DRAFT","ARCHIVED"))
            if committed + row["minutes"] > row["capacity"]:
                raise RetainerError(f"Insufficient capacity: {committed} reserved + {row['minutes']} proposed exceeds {row['capacity']} minutes.")
            row["state"] = "REVIEWED"
            row["reviewed_at"] = now()
            self._save(db,row)
        return row

    def archive(self,key):
        with self.connect() as db:
            current = self._read(db,key)
            has_operations = db.execute("SELECT 1 FROM operations WHERE offer_id=?", (key,)).fetchone()
        if has_operations:
            if not current.get("subscription_id"):
                raise RetainerError("An unfinished provider operation exists. Capacity stays reserved; inspect PayPal.")
            current = self.refresh(key)
            if current["provider_status"] not in ("CANCELLED","EXPIRED"):
                raise RetainerError("PayPal has not confirmed cancellation or expiration. Capacity stays reserved.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._read(db,key)
            operations_now = db.execute("SELECT 1 FROM operations WHERE offer_id=?", (key,)).fetchone()
            if operations_now and (not has_operations or row.get("provider_status") not in ("CANCELLED","EXPIRED")):
                raise RetainerError("A provider operation started during review. Capacity stays reserved.")
            row["state"] = "ARCHIVED"
            self._save(db,row)
        return row

    def create_resource(self,key,kind,confirmation,return_url):
        if kind not in ("product","plan","subscription"):
            raise RetainerError("Unknown resource.")
        if confirmation != f"CREATE {kind.upper()} {key[:8]}":
            raise RetainerError("Type the exact confirmation shown for this resource.")
        # Missing config fails before reservation. OAuth failure explicitly classified below.
        gateway = self.gateway_factory()
        expected = {"product":"REVIEWED","plan":"PRODUCT_CREATED","subscription":"PLAN_CREATED"}[kind]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._read(db,key)
            if row["state"] != expected:
                raise RetainerError("This resource is not ready or an earlier outcome is uncertain. Refresh the desk.")
            payload = payload_for(row,kind,return_url)
            existing = db.execute("SELECT request_id,state FROM operations WHERE offer_id=? AND kind=?", (key,kind)).fetchone()
            if existing and existing[1] != "NOT_SENT":
                raise RetainerError("This request already exists; it will not be replayed.")
            request_id = existing[0] if existing else secrets.token_hex(16)
            db.execute("INSERT OR REPLACE INTO operations VALUES (?,?,?,?,?,NULL)",
                (key,kind,request_id,"UNKNOWN",json.dumps(payload)))
            row["state"] = kind.upper() + "_UNKNOWN"
            self._save(db,row)
        try:
            result = gateway.create(kind,payload,request_id)
            identifier = result.get("id") if isinstance(result,dict) else None
            if not isinstance(identifier,str) or not re.fullmatch(r"[A-Za-z0-9-]{3,80}",identifier):
                raise RetainerError("Provider returned no usable identifier; outcome is uncertain.")
            if kind == "subscription":
                if result.get("status") != "APPROVAL_PENDING":
                    raise RetainerError("Provider returned unexpected subscription status; inspect PayPal before proceeding.")
                links = [x.get("href") for x in result.get("links",[]) if x.get("rel") == "approve"]
                from urllib.parse import urlsplit
                if len(links) != 1 or urlsplit(links[0]).scheme != "https" or urlsplit(links[0]).hostname != "www.sandbox.paypal.com":
                    raise RetainerError("Provider approval link missing or outside PayPal Sandbox; inspect provider outcome.")
        except PayPalRequestNotSent:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row=self._read(db,key)
                row["state"]=expected
                self._save(db,row)
                db.execute("UPDATE operations SET state='NOT_SENT' WHERE offer_id=? AND kind=?",(key,kind))
            raise RetainerError("Authentication failed before the resource request. Correct credentials and retry.") from None
        except Exception as exc:
            raise RetainerError("Provider outcome uncertain. No replay is allowed. Inspect PayPal using the saved request ID.") from exc
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=self._read(db,key)
            row[kind+"_id"]=identifier
            row["state"] = "AWAITING_BUYER" if kind == "subscription" else kind.upper()+"_CREATED"
            if kind == "subscription":
                row["approval_url"]=links[0]
                row["provider_status"]="APPROVAL_PENDING"
            self._save(db,row)
            db.execute("UPDATE operations SET state='CONFIRMED',result=? WHERE offer_id=? AND kind=?",(json.dumps(result),key,kind))
        return row


    def reconcile(self, key, kind, identifier):
        """Read an exact candidate ID; never replay or infer absence from a failed GET."""
        if kind not in ("product", "plan", "subscription"):
            raise RetainerError("Unknown resource.")
        if not isinstance(identifier,str) or not re.fullmatch(r"[A-Za-z0-9-]{3,80}",identifier):
            raise RetainerError("Enter the exact Sandbox resource ID.")
        with self.connect() as db:
            row = self._read(db,key)
            op = db.execute("SELECT request_id,state,payload FROM operations WHERE offer_id=? AND kind=?",(key,kind)).fetchone()
            if row["state"] != kind.upper()+"_UNKNOWN" or not op or op[1] != "UNKNOWN":
                raise RetainerError("Only an uncertain resource request can be recovered.")
            payload = json.loads(op[2])
            if kind in ("product","plan") and ("[Ledgerly offer: "+key+"]") not in payload.get("description",""):
                raise RetainerError("This older request has no offer identity marker. It cannot be safely attached automatically; keep the saved request ID for PayPal support.")
            plan_op = db.execute("SELECT payload FROM operations WHERE offer_id=? AND kind='plan'",(key,)).fetchone()
        gateway = self.gateway_factory()
        try:
            result = gateway.resource(kind,identifier)
            if not isinstance(result,dict) or result.get("id") != identifier:
                raise RetainerError("The returned resource ID does not match the candidate.")
            if kind in ("product","plan"):
                if not matches_payload(payload,result):
                    raise RetainerError("Provider identity or reviewed terms do not match the saved request.")
                if kind == "plan":
                    validate_plan(result)
            else:
                if result.get("plan_id") != payload["plan_id"] or result.get("custom_id") != key:
                    raise RetainerError("Provider subscription identity does not match this offer.")
                if result.get("plan_overridden") or result.get("plan") or str(result.get("quantity","1")) != "1":
                    raise RetainerError("Subscription overrides differ from the reviewed monthly offer.")
                if result.get("status") not in ("APPROVAL_PENDING","APPROVED","ACTIVE","SUSPENDED","CANCELLED","EXPIRED"):
                    raise RetainerError("Provider subscription status is not recognized.")
                if not plan_op:
                    raise RetainerError("The original plan terms are unavailable.")
                plan = gateway.resource("plan",payload["plan_id"])
                expected_plan = json.loads(plan_op[0])
                expected_plan.pop("status",None)
                if not isinstance(plan,dict) or plan.get("status") not in ("ACTIVE","INACTIVE"):
                    raise RetainerError("Provider plan status is not recognized.")
                if not isinstance(plan,dict) or plan.get("id") != payload["plan_id"] or not matches_payload(expected_plan,plan):
                    raise RetainerError("Current provider plan differs from the reviewed monthly terms.")
                validate_plan(plan)
        except RetainerError:
            raise
        except Exception:
            raise RetainerError("Provider read did not confirm this resource. The uncertain request remains reserved and will not be resent.") from None
        observed = now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._read(db,key)
            current = db.execute("SELECT request_id,state,payload FROM operations WHERE offer_id=? AND kind=?",(key,kind)).fetchone()
            if row["state"] != kind.upper()+"_UNKNOWN" or current != op:
                raise RetainerError("This request changed during the read. Reload the desk.")
            if any(other["id"] != key and other.get(kind+"_id") == identifier for other in self._rows(db)):
                raise RetainerError("This resource is already attached to a different offer.")
            row[kind+"_id"] = identifier
            row["state"] = "AWAITING_BUYER" if kind == "subscription" else kind.upper()+"_CREATED"
            row["recovered_at"] = observed
            row["recovery_resource"] = kind
            if kind == "subscription":
                row["provider_status"] = result["status"]
                row["provider_observed_at"] = observed
                from urllib.parse import urlsplit
                links = [x.get("href") for x in result.get("links",[]) if isinstance(x,dict) and x.get("rel")=="approve"]
                if len(links)==1 and isinstance(links[0],str) and urlsplit(links[0]).scheme=="https" and urlsplit(links[0]).hostname=="www.sandbox.paypal.com":
                    row["approval_url"] = links[0]
            self._save(db,row)
            db.execute("UPDATE operations SET state='CONFIRMED',result=? WHERE offer_id=? AND kind=?",(json.dumps(result),key,kind))
        return row

    def refresh(self,key):
        with self.connect() as db:
            row=self._read(db,key)
        if not row.get("subscription_id"):
            raise RetainerError("No confirmed subscription ID is available.")
        result=self.gateway_factory().subscription(row["subscription_id"])
        if result.get("id") != row["subscription_id"] or result.get("plan_id") != row["plan_id"] or result.get("custom_id") != key:
            raise RetainerError("Provider subscription identity does not match this reviewed offer.")
        status=result.get("status")
        if status not in ("APPROVAL_PENDING","APPROVED","ACTIVE","SUSPENDED","CANCELLED","EXPIRED"):
            raise RetainerError("Unknown provider status; previous observation retained.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=self._read(db,key)
            row["provider_status"]=status
            row["provider_observed_at"]=now()
            # Status is not a successful charge; capacity intentionally remains reserved.
            self._save(db,row)
        return row

def main():
    parser=argparse.ArgumentParser(description="Ledgerly monthly retainer desk (local; Sandbox only)")
    parser.add_argument("--store",default="~/.local/state/ledgerly/retainers.sqlite3")
    parser.add_argument("--port",type=int,default=8768)
    args=parser.parse_args()
    from .retainer_web import serve
    serve(RetainerDesk(args.store),args.port)

if __name__ == "__main__":
    main()
