"""The admin pages and the set-password page (docs/SERVER.md, "Admin pages"): server-rendered HTML, no JavaScript, one
stylesheet. Admins only; cloud/server.py hands every /admin and /set-password request here with its handler.

Every value on a page is HTML-escaped. Every POST must come with an Origin header equal to the server's own; the forms
behind the admin sign-in also carry a CSRF field, HMAC-SHA256 of the admin token keyed with settings.csrf_secret, and
the cookie is SameSite=Strict. Standard library only.
"""
import hashlib
import hmac
import html
from http.cookies import CookieError, SimpleCookie
from urllib.parse import parse_qs, urlsplit

import users

COOKIE = "fpp_admin"
STYLE = b"""body{font:15px/1.4 system-ui,sans-serif;margin:0;background:#f7f7f5;color:#1d1d1b}
main{max-width:1100px;margin:0 auto;padding:16px}h1{font-size:22px}h2{font-size:17px;margin-top:28px}
table{border-collapse:collapse;width:100%;background:#fff;font-size:14px}th,td{border:1px solid #ddd;padding:4px 8px;
text-align:left;vertical-align:top}form{display:inline}form.block{display:block;margin:8px 0}input,select,button{font:inherit}
input[type=text],input[type=email],input[type=password]{width:min(320px,100%)}label{display:block;margin:6px 0}
.note{background:#eef6ee;border:1px solid #9c9;padding:8px;word-break:break-all}.error{background:#fbeeee;
border:1px solid #c99;padding:8px}.wrap{overflow-x:auto}
@media (prefers-color-scheme:dark){body{background:#1b1b1a;color:#eee}table{background:#262624}th,td{border-color:#444}
.note{background:#1f3320;border-color:#4a4}.error{background:#3a1f1f;border-color:#a44}}
"""


def e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def page(title: str, body: str) -> bytes:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{e(title)}</title>'
            f'<link rel="stylesheet" href="/admin/style.css"></head><body><main>{body}</main></body></html>').encode()


def send_page(h, status: int, title: str, body: str, headers: dict | None = None):
    h.reply(status, {"Content-Type": "text/html; charset=utf-8", **(headers or {})}, page(title, body))


def redirect(h, location: str, cookie: str | None = None):
    h.reply(303, {"Location": location, **({"Set-Cookie": cookie} if cookie else {})}, b"")


def form_fields(raw: bytes) -> dict:
    return {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True,
                                         max_num_fields=50).items()}


def cookie_token(h) -> str | None:
    try:
        c = SimpleCookie(h.headers.get("Cookie", ""))
    except CookieError:
        return None
    return c[COOKIE].value if COOKIE in c else None


def csrf(db, token: str) -> str:
    return hmac.new(bytes.fromhex(users.setting(db, "csrf_secret")), token.encode(), hashlib.sha256).hexdigest()


def handle(h, db, path: str, raw: bytes):
    if path == "/admin/style.css" and h.command == "GET":
        return h.reply(200, {"Content-Type": "text/css; charset=utf-8"}, STYLE)
    if h.command == "POST" and not h.same_origin():
        return send_page(h, 403, "Refused", '<p class="error">Refused: this form did not come from this server.</p>')
    if path == "/set-password":
        return set_password(h, db, raw)
    if path == "/admin/signin":
        return signin(h, db, raw)
    token = cookie_token(h)
    me = users.person_of(db, token, "admin")
    if me is None or not me["admin"]:
        return redirect(h, "/admin/signin")
    if h.command == "GET" and path == "/admin":
        return send_page(h, 200, "FinePoint Perception admin", admin_page(db, token))
    if h.command != "POST" or path not in ACTIONS:
        return send_page(h, 404, "Not found", "<p>There is no such page.</p>")
    f = form_fields(raw)
    if not hmac.compare_digest(f.get("csrf", "").encode(), csrf(db, token).encode()):
        return send_page(h, 403, "Refused", '<p class="error">Refused: the form is stale. Open the admin page again.</p>')
    try:
        note = ACTIONS[path](h, db, me, f)
    except users.Refused as err:
        return send_page(h, 400, "FinePoint Perception admin", admin_page(db, token, error=str(err)))
    if note is None:
        return
    if note == "":
        return redirect(h, "/admin")
    send_page(h, 200, "FinePoint Perception admin", admin_page(db, token, note=note))


# ---- sign-in and set-password, before any admin cookie ----

def signin_form(message: str = "") -> str:
    return (f"<h1>FinePoint Perception admin</h1>{message}"
            '<form class="block" method="post" action="/admin/signin">'
            '<label>Email <input type="email" name="email" autocomplete="username" required></label>'
            '<label>Password <input type="password" name="password" autocomplete="current-password" required></label>'
            "<button>Sign in</button></form>")


def signin(h, db, raw: bytes):
    if h.command == "GET":
        return send_page(h, 200, "Sign in", signin_form())
    f = form_fields(raw)
    try:
        row = users.signin(db, f.get("email", ""), f.get("password", ""), h.client())
    except users.TooManyTries:
        return send_page(h, 429, "Sign in", signin_form('<p class="error">Too many failed sign-ins: wait 15 minutes.</p>'))
    if row is None or not row["admin"]:
        return send_page(h, 401, "Sign in", signin_form('<p class="error">Wrong email or password.</p>'))
    token = users.new_token(db, row["id"], "admin")
    cookie = f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={users.HOURS['admin'] * 3600}"
    redirect(h, "/admin", cookie + ("; Secure" if h.https() else ""))


def setup_owner(db, token: str):
    return db.execute(
        "SELECT u.email FROM tokens t JOIN users u ON u.id = t.user_id WHERE t.hash = ? AND t.kind = 'setup'"
        " AND t.revoked_at IS NULL AND t.expires_at > ? AND u.disabled_at IS NULL",
        (users.token_hash(token), users.now())).fetchone()


def password_form(token: str, email: str, message: str = "") -> str:
    return (f"<h1>Set your password</h1><p>For {e(email)}.</p>{message}"
            '<form class="block" method="post" action="/set-password">'
            f'<input type="hidden" name="t" value="{e(token)}">'
            f'<input type="email" name="email" value="{e(email)}" autocomplete="username" hidden>'
            f'<label>New password ({users.MIN_PASSWORD} to {users.MAX_PASSWORD} characters) <input type="password" '
            'name="password" autocomplete="new-password" required></label>'
            '<label>The same again <input type="password" name="again" autocomplete="new-password" required></label>'
            "<button>Set password</button></form>")


def set_password(h, db, raw: bytes):
    gone = '<h1>Set your password</h1><p class="error">This link has expired or was used. Ask your admin for a new one.</p>'
    if h.command == "GET":
        token = parse_qs(urlsplit(h.path).query).get("t", [""])[0]
    else:
        f = form_fields(raw)
        token = f.get("t", "")
    owner = setup_owner(db, token) if token else None
    if owner is None:
        return send_page(h, 410, "Set your password", gone)
    if h.command == "GET":
        return send_page(h, 200, "Set your password", password_form(token, owner["email"]))
    try:
        password = users.check_new_password(f.get("password"), f.get("again"))
    except users.Refused as err:
        return send_page(h, 400, "Set your password", password_form(token, owner["email"], f'<p class="error">{e(err)}.</p>'))
    if not users.use_setup_token(db, token, password):
        return send_page(h, 410, "Set your password", gone)
    send_page(h, 200, "Password set", "<h1>Password set</h1><p>Sign in from the FinePoint Perception app.</p>")


# ---- the forms behind the admin cookie: each returns a note to show, "" to go back to /admin, None when done ----

def add_lab(h, db, me, f):
    users.add_lab(db, f.get("name", "").strip(), f.get("title", "").strip())
    return ""


def add_user(h, db, me, f):
    uid = users.add_user(db, f.get("email", ""), f.get("lab", ""), f.get("name", "").strip(), f.get("admin") == "1")
    return f"Send this set-password link to {f.get('email', '').strip()} (shown once): " + users.setup_link(
        db, uid, h.server.settings["public_url"])


def person(db, f):
    row = db.execute("SELECT id, email FROM users WHERE id = ?", (f.get("user", ""),)).fetchone()
    if row is None:
        raise users.Refused("there is no such person")
    return row


def new_link(h, db, me, f):
    row = person(db, f)
    return f"Send this set-password link to {row['email']} (shown once): " + users.setup_link(
        db, row["id"], h.server.settings["public_url"])


def disable(h, db, me, f):
    users.set_disabled(db, person(db, f)["id"], True)
    return ""


def enable(h, db, me, f):
    users.set_disabled(db, person(db, f)["id"], False)
    return ""


def signout_computer(h, db, me, f):
    db.execute("UPDATE tokens SET revoked_at = ? WHERE hash = ? AND kind = 'app' AND revoked_at IS NULL",
               (users.now(), f.get("computer", "")))
    return ""


def signout(h, db, me, f):
    users.revoke(db, me["token"])
    redirect(h, "/admin/signin", f"{COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
             + ("; Secure" if h.https() else ""))
    return None


ACTIONS = {"/admin/add-lab": add_lab, "/admin/add-user": add_user, "/admin/link": new_link, "/admin/disable": disable,
           "/admin/enable": enable, "/admin/signout-computer": signout_computer, "/admin/signout": signout}


# ---- the page ----

def size_text(n) -> str:
    n = n or 0
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"


def table(head: list[str], rows: list[list[str]]) -> str:
    """head and cells are already escaped HTML"""
    if not rows:
        return "<p>None yet.</p>"
    th = "".join(f"<th>{c}</th>" for c in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="wrap"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def button(action: str, token_csrf: str, label: str, **fields) -> str:
    hidden = "".join(f'<input type="hidden" name="{e(k)}" value="{e(v)}">' for k, v in fields.items())
    return (f'<form method="post" action="{e(action)}"><input type="hidden" name="csrf" value="{e(token_csrf)}">'
            f"{hidden}<button>{e(label)}</button></form>")


def sessions(db) -> list[list[str]]:
    newest = {(r["lab"], r["session"], r["path"]): r for r in db.execute(
        "SELECT * FROM uploads WHERE id IN (SELECT max(id) FROM uploads GROUP BY lab, session, path)")}
    out = []
    for s in db.execute(
            "SELECT up.lab, up.session, max(coalesce(up.finished_at, up.started_at)) AS last,"
            " sum(up.status = 'uploading') AS open, group_concat(DISTINCT u.email) AS who"
            " FROM uploads up LEFT JOIN users u ON u.id = up.user_id GROUP BY up.lab, up.session ORDER BY last DESC"):
        rows = [r for k, r in newest.items() if k[:2] == (s["lab"], s["session"])]
        done = [r for r in rows if r["status"] in ("done", "already_there")]
        manifest = newest.get((s["lab"], s["session"], "manifest.json"))
        complete = manifest is not None and manifest["status"] in ("done", "already_there") and not s["open"]
        out.append([e(s["lab"]), e(s["session"]), e(f"{len(done)} of {len(rows)}"),
                    e(size_text(sum(r["size"] for r in done))), e(s["last"]), e(s["who"]),
                    e("complete" if complete else "in progress")])
    return out


def admin_page(db, token: str, note: str = "", error: str = "") -> str:
    c = csrf(db, token)
    people = []
    for u in db.execute("SELECT u.*, (SELECT max(created_at) FROM tokens WHERE user_id = u.id AND kind IN"
                        " ('app', 'admin')) AS last_signin FROM users u ORDER BY u.lab, u.email"):
        state = "disabled" if u["disabled_at"] else "invited" if u["password"] is None else "active"
        actions = button("/admin/link", c, "New link", user=u["id"]) + " " + (
            button("/admin/enable", c, "Enable", user=u["id"]) if u["disabled_at"]
            else button("/admin/disable", c, "Disable", user=u["id"]))
        people.append([e(u["email"]), e(u["name"]), e(u["lab"]), e("yes" if u["admin"] else ""), e(state),
                       e(u["last_signin"]), actions])
    labs = [[e(r["name"]), e(r["title"]), e(r["people"]), e(r["sessions"])] for r in db.execute(
        "SELECT l.name, l.title, (SELECT count(*) FROM users WHERE lab = l.name) AS people,"
        " (SELECT count(DISTINCT session) FROM uploads WHERE lab = l.name) AS sessions FROM labs l ORDER BY l.name")]
    computers = [[e(r["email"]), e(r["label"]), e(r["created_at"]), e(r["last_used_at"]),
                  button("/admin/signout-computer", c, "Sign out", computer=r["hash"])] for r in db.execute(
        "SELECT t.hash, t.label, t.created_at, t.last_used_at, u.email FROM tokens t JOIN users u ON u.id = t.user_id"
        " WHERE t.kind = 'app' AND t.revoked_at IS NULL ORDER BY u.email, t.created_at")]
    lab_options = "".join(f'<option value="{e(r["name"])}">{e(r["name"])}</option>'
                          for r in db.execute("SELECT name FROM labs ORDER BY name"))
    return (
        "<h1>FinePoint Perception admin</h1>" + button("/admin/signout", c, "Sign out")
        + (f'<p class="note">{e(note)}</p>' if note else "") + (f'<p class="error">{e(error)}</p>' if error else "")
        + "<h2>Uploads by session</h2>"
        + table(["Lab", "Session", "Files done", "Size", "Last activity", "Who", "State"], sessions(db))
        + "<h2>People</h2>" + table(["Email", "Name", "Lab", "Admin", "State", "Last sign-in", ""], people)
        + f'<form class="block" method="post" action="/admin/add-user"><input type="hidden" name="csrf" value="{e(c)}">'
        '<label>Email <input type="email" name="email" required></label><label>Name <input type="text" name="name">'
        f'</label><label>Lab <select name="lab" required>{lab_options}</select></label>'
        '<label><input type="checkbox" name="admin" value="1"> Admin</label><button>Add person</button></form>'
        + "<h2>Labs</h2>" + table(["Name", "Title", "People", "Sessions"], labs)
        + f'<form class="block" method="post" action="/admin/add-lab"><input type="hidden" name="csrf" value="{e(c)}">'
        '<label>Name (a-z, 0-9, -) <input type="text" name="name" required></label>'
        '<label>Title <input type="text" name="title" required></label><button>Add lab</button></form>'
        + "<h2>Computers</h2>" + table(["Person", "Computer", "Signed in", "Last used", ""], computers))
