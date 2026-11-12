"""Local HTTP UI for the same persistent Ledgerly retainer desk."""
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from .retainer import RetainerError

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass
    def allowed(self):
        port=self.server.server_port
        hosts=(f"127.0.0.1:{port}",f"localhost:{port}")
        return self.headers.get("Host") in hosts and self.headers.get("Origin") in (None,*(f"http://{x}" for x in hosts))
    def reply(self,status,data,html=False):
        raw=data.encode() if html else json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type","text/html; charset=utf-8" if html else "application/json")
        self.send_header("Content-Length",str(len(raw)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Frame-Options","DENY")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Referrer-Policy","no-referrer")
        self.end_headers()
        self.wfile.write(raw)
    def do_GET(self):
        if not self.allowed():
            return self.reply(403,{"error":"Local origin required."})
        if self.path in ("/","/?returned=1"):
            page=files("ledgerly").joinpath("retainer_web_ui.html").read_text(encoding="utf-8")
            return self.reply(200,page.replace("__TOKEN__",self.server.token),True)
        if self.path=="/api/desk" and self.headers.get("X-Ledgerly-Token")==self.server.token:
            return self.reply(200,self.server.desk.snapshot())
        return self.reply(404,{"error":"Not found."})
    def do_POST(self):
        if not self.allowed() or self.headers.get("X-Ledgerly-Token")!=self.server.token:
            return self.reply(403,{"error":"Local session required."})
        try:
            size=int(self.headers.get("Content-Length","0"))
            if size<1 or size>20000:raise RetainerError("Request too large or empty.")
            body=json.loads(self.rfile.read(size))
            if not isinstance(body,dict):raise RetainerError("JSON object required.")
            desk=self.server.desk
            if self.path=="/api/delivery":result=desk.save_delivery(body)
            elif self.path=="/api/delivery-state":result=desk.set_delivery_state(body.get("id"),body.get("state"),body.get("revision"))
            elif self.path=="/api/draft":result=desk.save_draft(body)
            elif self.path=="/api/approve":result=desk.approve(body.get("id"),body.get("review_hash"))
            elif self.path=="/api/archive":result=desk.archive(body.get("id"))
            elif self.path=="/api/create":result=desk.create_resource(body.get("id"),body.get("kind"),body.get("confirmation"),f"http://127.0.0.1:{self.server.server_port}/?returned=1")
            elif self.path=="/api/reconcile":result=desk.reconcile(body.get("id"),body.get("kind"),body.get("identifier"))
            elif self.path=="/api/refresh":result=desk.refresh(body.get("id"))
            else:return self.reply(404,{"error":"Not found."})
            return self.reply(200,result)
        except (ValueError,TypeError,KeyError) as e:
            return self.reply(400,{"error":str(e)})
        except Exception:
            return self.reply(503,{"error":"Request could not complete. Reload the desk to inspect its saved state."})

def server(desk,port=0):
    http=ThreadingHTTPServer(("127.0.0.1",port),Handler)
    http.desk=desk
    http.token=secrets.token_hex(24)
    return http

def serve(desk,port):
    http=server(desk,port)
    print(f"Ledgerly retainer desk: http://127.0.0.1:{http.server_port}",flush=True)
    try:http.serve_forever()
    except KeyboardInterrupt:pass
    finally:http.server_close()
