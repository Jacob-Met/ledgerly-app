import json
from datetime import date,datetime,time,timezone
from decimal import Decimal
from ledgerly.extract import source_references, DEFAULT_INVOICE_NOTE
from ledgerly.agent import Agent,RulePlanner,ApprovalRequired
from ledgerly.paypal import SandboxMock
from ledgerly.extract import PAYPAL_CURRENCIES, split_by_currency, LLM_PROMPT, LLMExtractor
from review import ReviewableExtractor, prepare_review, ReviewFieldError, source_amount_requirements, check_source_amount_resolutions
from invoice_details import snapshot_invoice_details
from uuid import uuid4

class Clock:
    def __init__(self): self.day=date.today()
    def __call__(self): return self.day

class Demo:
    statement_source = "browser-simulation"
    def __init__(self):
        self.clock=Clock()
        self.mock=SandboxMock(now=lambda:datetime.combine(self.clock.day,time.min,tzinfo=timezone.utc))
        self.extractor=ReviewableExtractor()
        self.agent=Agent(self.mock,{"name":"Ledgerly Demo","email_address":"freelancer@example.test"},extractor=self.extractor,today=self.clock,webhook_verifier=self.mock.verify_webhook_signature)
        self.last_event=None
        self.invoice_sources={}
        self.analysis_text=None
        self.analysis=None
        self.review=None
        self.ai_requires_review=False

    def draft(self, text):
        previous=set(self.agent.ledger)
        out=self.agent.run("invoice:"+text,RulePlanner());pending=out.get("pending",[]);blocked=[]
        for identity in set(self.agent.ledger)-previous:
            self.invoice_sources[identity]=text
        for action in pending:
            if action.get("kind")=="send_invoice":
                try:self.agent.client.send_invoice(action["invoice_id"]);blocked.append(False)
                except ApprovalRequired:blocked.append(True)
        return {"final":out.get("final",""),"pending":pending,"unauthorized_send_blocked":bool(blocked) and all(blocked)}

    @staticmethod
    def totals(ex):
        return [{"currency":part.currency or "Unknown currency","total":str(part.total())} for part in split_by_currency(ex)]

    @staticmethod
    def financials(ex):
        # Use the same Decimal totals and currency split as invoice creation.
        # An ambiguous or invalid proposal has no confirmed amount-due summary.
        if ex.errors:
            return []
        return [{"currency": part.currency or "Unknown currency",
                 "total": str(part.total()), "paid": str(part.amount_paid),
                 "due": str(part.total() - part.amount_paid)}
                for part in split_by_currency(ex)]

    @staticmethod
    def line_amounts(ex):
        # Reuse the same Decimal calculation as the authoritative invoice total.
        return [str(item.amount()) if item.qty is not None and item.amount().is_finite() else None
                for item in ex.line_items]

    def snapshot(self):
        audit=[]
        for row in [row for row in self.agent.audit if row.get("event") != "tool" or row.get("ok") is False][-18:]:
            audit.append({k:row[k] for k in ("at","event","action","kind","invoice","ok","event_type","from","to") if k in row})
        return {"today":self.clock.day.isoformat(),"ledger":[e.to_dict() for e in self.agent.ledger.values()],"invoice_details":snapshot_invoice_details(self.mock.invoices,self.agent.ledger),"invoice_requests":[identity for identity in self.agent.ledger if identity in self.invoice_sources],"pending":self.agent.list_pending(),"completed_reviews":[a.to_dict() for a in self.agent.pending.values() if a.status != "PENDING"],"audit":audit,"mock_requests":len(self.mock.requests),"external_calls":0,"can_replay":self.last_event is not None}
    def dispatch(self,req):
        action=req.get("action")
        if action=="init": result={"ready":True,"engine":"RulesExtractor + RulePlanner + SandboxMock"}
        elif action=="invoice_request":
            identity=req.get("invoice_id")
            if not isinstance(identity,str) or identity not in self.agent.ledger or identity not in self.invoice_sources:
                raise ValueError("The original request was not saved with this invoice.")
            entry=self.agent.ledger[identity]
            result={"invoice_id":identity,"invoice_number":entry.invoice_number,
                    "client_name":entry.client_name,"source_text":self.invoice_sources[identity]}
        elif action=="client_statement":
            from ledgerly.statement import render_statement
            invoice_id = req.get("invoice_id")
            if not isinstance(invoice_id, str) or invoice_id not in self.agent.ledger:
                raise ValueError("Choose an invoice from the current saved ledger")
            email = self.agent.ledger[invoice_id].client_email
            document = render_statement(self.agent, email, as_of=self.clock.day, source=self.statement_source)
            safe_email = "".join(c if c.isascii() and c.isalnum() else "-" for c in email).strip("-")[:96]
            result = {"html": document, "client_email": email,
                      "record_count": sum(e.client_email.casefold() == email.casefold() for e in self.agent.ledger.values()),
                      "filename": "ledgerly-client-statement-" + safe_email + "-" + self.clock.day.isoformat() + ".html"}
        elif action=="import_email":
            # Preview only: MIME decoding never changes the current analysis,
            # checked revision, invoices or approvals.
            import base64
            from ledgerly.email_input import parse_eml, MAX_EML_BYTES
            encoded=req.get("email_base64")
            if not isinstance(encoded,str) or len(encoded)>4*((MAX_EML_BYTES+2)//3):
                raise ValueError("Choose an exported email no larger than 2 MiB.")
            try:
                raw=base64.b64decode(encoded,validate=True)
            except (ValueError,TypeError) as exc:
                raise ValueError("The exported email bytes could not be read.") from exc
            result={"source_text":parse_eml(raw)}
        elif action=="ai_prompt":
            text=req.get("text")
            if not isinstance(text,str) or not text.strip() or len(text)>30000:
                raise ValueError("Provide a job email of at most 30,000 characters for local AI.")
            result={"prompt":LLM_PROMPT.replace("{text}",text)}
        elif action=="analyze_ai":
            text=req.get("text")
            completion=req.get("completion")
            if not isinstance(text,str) or not text.strip() or len(text)>30000:
                raise ValueError("Provide a job email of at most 30,000 characters for local AI.")
            if not isinstance(completion,str) or len(completion)>64000:
                raise ValueError("Local AI response must be text of at most 64,000 characters.")
            ex=LLMExtractor(lambda _:completion, check_missing_work=True).extract(text)
            # The core reports malformed model output as an unusable extraction,
            # not an exception. It must not replace a previously checked invoice.
            for issue in ex.errors:
                if issue.field=="*" and issue.message.startswith("LLM output unusable:"):
                    raise ValueError(issue.message)
            analysis={**ex.to_dict(),"source_references":source_references(ex,text)}
            result={**analysis,"review_currencies":sorted(PAYPAL_CURRENCIES),"totals_by_currency":self.totals(ex),"financials_by_currency":self.financials(ex),"line_amounts":self.line_amounts(ex),
                    "requires_review":True}
            # Preserve the previous checked revision if parsing or materializing
            # this proposal fails. A completed proposal needs a fresh review.
            self.review=None
            self.analysis_text=text
            self.analysis=analysis
            self.ai_requires_review=True
            result["source_amount_requirements"]=source_amount_requirements(self.analysis)
        elif action=="analyze":
            self.ai_requires_review=False
            self.review=None
            self.analysis=None
            if not isinstance(req.get("text",""),str):raise ValueError("Source email must be text.")
            self.analysis_text=req.get("text","")
            ex=self.agent.extractor.extract(self.analysis_text)
            self.analysis={**ex.to_dict(),"source_references":source_references(ex,self.analysis_text)}
            result={**self.analysis,"review_currencies":sorted(PAYPAL_CURRENCIES),"totals_by_currency":self.totals(ex),"financials_by_currency":self.financials(ex),"line_amounts":self.line_amounts(ex)}
            result["source_amount_requirements"]=source_amount_requirements(self.analysis)
        elif action=="review":
            self.review=None
            if self.analysis is None or req.get("text")!=self.analysis_text:
                raise ValueError("The source changed after analysis; analyze it again before reviewing.")
            if req.get("confirmed") is not True:
                raise ValueError("Review every invoice field and confirm the source warnings first.")
            fields=req.get("fields")
            if isinstance(fields,dict) and "note" not in fields and self.analysis.get("note",DEFAULT_INVOICE_NOTE)!=DEFAULT_INVOICE_NOTE:
                raise ReviewFieldError("note","Review the client instructions in the invoice note before checking these fields.")
            ex=prepare_review(fields)
            check_source_amount_resolutions(ex,self.analysis,req.get("source_resolutions"),self.analysis_text)
            valid=not ex.errors and ex.confidence>=self.agent.min_confidence
            review_id=uuid4().hex if valid else None
            # Materialize every displayed value before admitting a draft revision.
            result={**ex.to_dict(),"valid":valid,"review_id":review_id,
                    "original_issues":self.analysis["issues"],
                    "source_amount_requirements":source_amount_requirements(self.analysis),
                    "totals_by_currency":self.totals(ex),"financials_by_currency":self.financials(ex),"line_amounts":self.line_amounts(ex),
                    "message":"Fields checked. Create the reviewed draft, then approve its send separately." if valid else "Correct the remaining issues, then check the fields again."}
            if valid:self.review=(review_id,self.analysis_text,ex)
        elif action=="draft":
            if self.ai_requires_review and "review_id" not in req:
                raise ValueError("On-device AI results require checked human fields before drafting.")
            text=req.get("text","")
            if "review_id" in req:
                checked=self.review
                self.review=None  # Consume before draft effects; never replay on retry.
                if checked is None or req["review_id"]!=checked[0] or text!=checked[1]:
                    raise ValueError("This checked revision is stale or already used; review and check it again.")
                _,source,ex=checked
                with self.extractor.using(source,ex):result=self.draft(text)
                result["reviewed"]=True
            else:result=self.draft(text)
        elif action=="approve":
            pending=next(a for a in self.agent.list_pending() if a["id"]==req["action_id"]);self.agent.approve(req["action_id"],approver="browser visitor");result={"approved":True,"kind":pending["kind"],"invoice_id":pending["invoice_id"]}
        elif action=="reopen_send_review":
            result=self.agent.reopen_send_review(req["action_id"])
        elif action=="inspect_draft_correction":
            result=self.agent.inspect_draft_correction(req["invoice_id"])
        elif action=="prepare_draft_correction":
            result=self.agent.prepare_draft_correction(req["invoice_id"],req.get("items"),req.get("revision"),req.get("recipient"),req.get("terms"),req.get("note"))
        elif action=="prepare_cancellation":
            result=self.agent.prepare_cancellation(req["invoice_id"],req.get("reason",""))
        elif action=="reject":
            reason=req.get("reason","")
            if not isinstance(reason,str):
                raise ValueError("Rejection reason must be text.")
            if len(reason)>500:
                raise ValueError("Use at most 500 characters for the rejection reason.")
            reason=reason if reason.strip() else "Rejected by the browser visitor"
            self.agent.reject(req["action_id"],reason)
            result={"rejected":True,"reason":reason}
        elif action=="payment":
            amount=req.get("amount");event=self.mock.simulate_payer_payment(req["invoice_id"],None if amount is None or amount=="" else Decimal(str(amount)));raw=json.dumps(event,separators=(",",":")).encode("utf-8");headers=self.mock.sign(raw);self.last_event=(raw,headers);result=self.agent.handle_webhook(raw,headers)
        elif action=="replay": result={"ok":False,"message":"No webhook to replay."} if self.last_event is None else self.agent.handle_webhook(*self.last_event)
        elif action=="advance":
            new_day=date.fromisoformat(req["day"])
            if new_day<self.clock.day: raise ValueError("Demo clock only moves forward.")
            self.clock.day=new_day;result={"today":self.clock.day.isoformat()}
        elif action=="remind_invoice":
            # One selected invoice only. The native Agent rechecks provider facts,
            # cooldown and pending approvals; it queues a draft, never sends it.
            invoice_id=req.get("invoice_id")
            if not isinstance(invoice_id,str) or not invoice_id:
                raise ValueError("Choose one invoice from the current sandbox ledger.")
            result=self.agent.tool_send_reminder(invoice_id)
            if not result["ok"]:
                raise ValueError(result["message"])
        elif action=="chase":
            # This fixed planner lists once, visits at most each current ledger entry,
            # then finishes. Keep the budget finite for this deterministic demo goal.
            steps=max(12,len(self.agent.ledger)+2)
            out=self.agent.run("chase",RulePlanner(),max_steps=steps)
            result={"final":out.get("final",""),"pending":out.get("pending",[])}
        elif action=="reset": self.__init__();result={"reset":True}
        else: raise ValueError("Unknown demo action.")
        return {"result":result,"state":self.snapshot()}

SESSION=Demo()
def handle_json(raw):
    try:
        response=SESSION.dispatch(json.loads(raw));return json.dumps({"ok":True,**response},default=str)
    except Exception as exc:
        return json.dumps({"ok":False,"error":type(exc).__name__,"message":str(exc),"state":SESSION.snapshot(),**({"issues":exc.issues} if isinstance(exc,ReviewFieldError) else {})},default=str)
