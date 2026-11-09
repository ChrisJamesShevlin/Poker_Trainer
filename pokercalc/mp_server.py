"""The multiplayer table's web side: the host's page and controls, the friend's page, the game code, and the gate that keeps a guest away from
everything else in the app.

How it is kept safe: the host's computer runs the whole app on 127.0.0.1 (only this computer can reach it). To let a friend in, the host presses
"Start sharing": a SECOND listener starts on the host's Tailscale address (a private network) and only lets through the pages and calls the
table needs (see GuestGuard). A guest also needs the game code. Nothing of the host's results, sessions or save is reachable from there.
"""
import hmac
import ipaddress
import json
import secrets
import shutil
import subprocess
import threading
import time

from flask import Blueprint, Response, current_app, jsonify, request
from werkzeug.serving import make_server

from . import service, stakes
from .game import IllegalAction
from .multiplayer import GUEST, HOST, SharedTable, clean_name

bp = Blueprint("multiplayer", __name__)
_lock = threading.RLock()
_table = None
_code = None
_guest_token = None
_listener = None                  # {"server": ..., "thread": ..., "ip": ..., "port": ...} while the guest listener runs
_fails = []                       # times of recent wrong game codes
HOST_TOKEN = secrets.token_urlsafe(16)          # only ever put into the host's own page (served on 127.0.0.1)
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"        # no 0/O/1/I to misread
GUEST_PORT = 5174
MAX_FAILS_PER_MINUTE = 8


def new_code():
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


def table():
    global _table, _code
    if _table is None:
        _table = SharedTable()
        _code = new_code()
    return _table


def reset_for_tests():
    """A fresh table, code and tokens (the tests call this)."""
    global _table, _code, _guest_token, _fails
    stop_sharing()
    _table, _code, _guest_token, _fails = None, None, None, []
    table()


# ------------------------------------------------------------------ the Tailscale address and the guest listener
def tailscale_ip():
    """The host's Tailscale IPv4 address (100.x.y.z), or None if Tailscale is not installed or not running."""
    exe = shutil.which("tailscale") or shutil.which("tailscale.exe")
    for cand in (exe, r"C:\Program Files\Tailscale\tailscale.exe", "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        if not cand:
            continue
        try:
            out = subprocess.run([cand, "ip", "-4"], capture_output=True, text=True, timeout=5).stdout.split()
            for token in out:
                if _is_tailscale(token):
                    return token
        except Exception:
            continue
    return None


def _is_tailscale(ip):
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False


class GuestGuard:
    """WSGI wrapper for the guest listener: only the friend's page, the calculator and the table's own calls get through."""
    OPEN_PAGES = {"/play", "/rta"}
    OPEN_API = {"/api/mp/join", "/api/mp/state", "/api/mp/deal", "/api/mp/act", "/api/mp/advice", "/api/mp/leave", "/api/mp/config"}

    def __init__(self, app, allow_local=False):
        self.app, self.allow_local = app, allow_local

    def _deny(self, start_response, code="403 Forbidden", msg="Not available here."):
        body = json.dumps({"error": msg}).encode()
        start_response(code, [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
        return [body]

    def __call__(self, environ, start_response):
        remote, path, method = environ.get("REMOTE_ADDR", ""), environ.get("PATH_INFO", ""), environ.get("REQUEST_METHOD", "GET")
        if not (_is_tailscale(remote) or (self.allow_local and remote in ("127.0.0.1", "::1"))):
            return self._deny(start_response, msg="Only people on the private network can join.")
        if path == "/api/session" and method == "GET":                # the calculator page asks for this on load: nothing to show a guest
            body = json.dumps({"active": None, "last_balance": None}).encode()
            start_response("200 OK", [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
            return [body]
        ok = path in self.OPEN_PAGES or path.startswith("/static/") or path in self.OPEN_API or (path == "/api/calc" and method == "POST")
        if not ok:
            return self._deny(start_response)
        environ["poker.guest_port"] = True
        return self.app(environ, start_response)


def start_sharing(ip=None, port=GUEST_PORT, allow_local=False):
    """Start the guest listener on the Tailscale address. Returns {"url": ...} or raises RuntimeError with something to tell the host."""
    global _listener
    with _lock:
        if _listener:
            return _share_info()
        ip = (ip or "").strip() or tailscale_ip()
        if not ip:
            raise RuntimeError("Could not find Tailscale. Is it installed and signed in on this computer? You can also type your Tailscale address (it starts with 100.).")
        if not (_is_tailscale(ip) or (allow_local and ip == "127.0.0.1")):
            raise RuntimeError("That does not look like a Tailscale address. It should start with 100. (find it in the Tailscale app).")
        for p in range(port, port + 20):
            try:
                srv = make_server(ip, p, GuestGuard(current_app._get_current_object(), allow_local=allow_local), threaded=True)
                break
            except OSError:
                continue
        else:
            raise RuntimeError("Could not open a port for your friend. Close other programs and try again.")
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        _listener = {"server": srv, "thread": t, "ip": ip, "port": p}
        return _share_info()


def stop_sharing():
    global _listener
    with _lock:
        if _listener:
            _listener["server"].shutdown()
            _listener["server"].server_close()
            _listener = None


def _share_info():
    return {"sharing": bool(_listener), "url": f"http://{_listener['ip']}:{_listener['port']}/play" if _listener else None}


# ------------------------------------------------------------------ who is asking
def _viewer():
    """HOST, GUEST, or None from the X-MP-Token header. The host's token only counts on the host's own (local) listener."""
    token = request.headers.get("X-MP-Token", "")
    if token and hmac.compare_digest(token, HOST_TOKEN) and not request.environ.get("poker.guest_port"):
        return HOST
    if token and _guest_token and hmac.compare_digest(token, _guest_token) and table().guest is not None:
        return GUEST
    return None


def _need(viewer):
    return jsonify(error="You are not at this table."), 401


def _reply(t, viewer, **extra):
    st = t.state(viewer)
    st.update(mp=True, role="host" if viewer == HOST else "guest", mode="multiplayer", locks={}, frames=[], notices=[], **extra)
    return jsonify(st)


# ------------------------------------------------------------------ pages
def _page(name, config=None):
    path = current_app.static_folder + "/" + name
    html = open(path, encoding="utf-8").read()
    marker = "<!--MP-->"
    html = html.replace(marker, f"<script>window.MP={json.dumps(config)};</script>" if config else "")
    return Response(html, mimetype="text/html")


@bp.get("/multiplayer")
def host_page():
    if request.environ.get("poker.guest_port"):
        return Response("Not available here.", status=403)
    return _page("trainer.html", {"role": "host", "token": HOST_TOKEN})


@bp.get("/play")
def guest_page():
    return _page("trainer.html", {"role": "guest", "token": None})


@bp.get("/rta")
def guest_rta():
    return Response(open(current_app.static_folder + "/index.html", encoding="utf-8").read(), mimetype="text/html")


# ------------------------------------------------------------------ the table's calls (host and guest)
@bp.get("/api/mp/config")
def config():
    with _lock:
        t = table()
        return jsonify(stakes=stakes.ladder(t.currency), strengths=stakes.strength_options(), currencies=list(stakes.CURRENCIES),
                       min_buy_in_bb=stakes.MIN_BUY_IN_BB, max_buy_in_bb=stakes.MAX_BUY_IN_BB, timers=[0], speeds=["instant"],
                       mode="multiplayer", locks={})


@bp.get("/api/mp/state")
def state():
    with _lock:
        v = _viewer()
        if v is None:
            return _need(v)
        t = table()
        if request.args.get("version") and str(t.version) == request.args["version"] and not request.args.get("force"):
            return jsonify(unchanged=True, version=t.version)
        return _reply(t, v)


@bp.post("/api/mp/deal")
def deal():
    with _lock:
        v = _viewer()
        if v is None:
            return _need(v)
        t = table()
        try:
            t.start_hand()
        except IllegalAction as e:
            return jsonify(error=str(e)), 400
        return _reply(t, v)


@bp.post("/api/mp/act")
def act():
    d = request.get_json() or {}
    kind = d.get("action")
    action = ("raise", int(d["amount"])) if kind == "raise" else kind
    with _lock:
        v = _viewer()
        if v is None:
            return _need(v)
        t = table()
        try:
            t.act(v, action)
        except (IllegalAction, ValueError, TypeError) as e:
            return jsonify(error=str(e)), 400
        return _reply(t, v)


@bp.post("/api/mp/advice")
def advice():
    d = request.get_json() or {}
    with _lock:
        v = _viewer()
        if v is None:
            return _need(v)
        try:
            spot = table().advice_input(v)
        except IllegalAction as e:
            return jsonify(error=str(e)), 400
        if d.get("opp_type") in ("folds", "normal", "calls"):
            spot["opp_type"] = d["opp_type"]
    out = service.evaluate(spot, sims=20000)
    hold = out.get("hold") or {}
    return jsonify(action=out["action"], why=out["why"], sizing=str(out["sizing"]) if out.get("sizing") else None, equity=out["equity"],
                   required=out["required"], range_label=out["range_label"], hold=hold.get("title"), draws=hold.get("draws"),
                   fold_equity=out.get("fold_equity"), bluff=out.get("bluff"))


@bp.post("/api/mp/join")
def join():
    """A friend enters the game code and a name."""
    global _guest_token
    d = request.get_json() or {}
    now = time.time()
    with _lock:
        _fails[:] = [x for x in _fails if now - x < 60]
        if len(_fails) >= MAX_FAILS_PER_MINUTE:
            return jsonify(error="Too many wrong codes. Wait a minute and try again."), 429
        table()
        if not hmac.compare_digest(str(d.get("code", "")).strip().upper(), _code):
            _fails.append(now)
            return jsonify(error="That game code is not right."), 403
        t = table()
        name = clean_name(d.get("name", ""), "Guest")
        if t.guest is not None and t.guest["name"] != name:
            return jsonify(error="Someone is already at the table. Ask the host to remove them first."), 409
        try:
            if t.guest is None:
                t.join(name)
        except IllegalAction:
            return jsonify(error="A hand is in progress. It will let you in as soon as it finishes.", wait=True), 409
        _guest_token = secrets.token_urlsafe(16)
        return jsonify(token=_guest_token, name=t.guest["name"])


@bp.post("/api/mp/leave")
def leave():
    with _lock:
        v = _viewer()
        if v != GUEST:
            return _need(v)
        table().request_leave()
        return jsonify(ok=True)


# ------------------------------------------------------------------ the host's controls (host token only)
def _host_only():
    return _viewer() == HOST


@bp.get("/api/mp/host")
def host_info():
    if not _host_only():
        return _need(None)
    with _lock:
        t = table()
        return jsonify(code=_code, guest=t.guest["name"] if t.guest else None, **_share_info(), tailscale=tailscale_ip() is not None)


@bp.post("/api/mp/share")
def share():
    if not _host_only():
        return _need(None)
    d = request.get_json() or {}
    with _lock:
        try:
            if d.get("on", True):
                info = start_sharing(d.get("ip") or None)
            else:
                stop_sharing()
                info = _share_info()
        except RuntimeError as e:
            return jsonify(error=str(e)), 400
        return jsonify(code=_code, **info)


@bp.post("/api/mp/newcode")
def newcode():
    global _code, _guest_token
    if not _host_only():
        return _need(None)
    with _lock:
        _code = new_code()
        return jsonify(code=_code)


@bp.post("/api/mp/kick")
def kick():
    global _guest_token
    if not _host_only():
        return _need(None)
    with _lock:
        t = table()
        t.request_leave()
        if t.guest is None:
            _guest_token = None
        return jsonify(ok=True)


@bp.post("/api/mp/settings")
def settings():
    if not _host_only():
        return _need(None)
    d = request.get_json() or {}
    with _lock:
        t = table()
        try:
            if d.get("stake") and d["stake"] != t.stake:
                t.set_stake(d["stake"], int(d["buy_in"]) if d.get("buy_in") is not None else None)
            elif d.get("buy_in") is not None and int(d["buy_in"]) != t.buy_in:
                t.set_buy_in(int(d["buy_in"]))
            if d.get("strength") and d["strength"] != t.strength:
                t.set_strength(d["strength"])
            if d.get("currency"):
                t.set_currency(d["currency"])
            if d.get("host_name") is not None:
                t.set_host_name(d["host_name"])
        except (ValueError, IllegalAction) as e:
            return jsonify(error=str(e)), 400
        return _reply(t, HOST)
