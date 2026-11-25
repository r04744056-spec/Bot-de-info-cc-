import asyncio, os, re, secrets, sqlite3, threading, time
from dotenv import load_dotenv
import requests
from flask import Flask, request as freq

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, ConversationHandler, MessageHandler, filters)

load_dotenv()
TOKEN, ADMIN = os.environ["BOT_TOKEN"], int(os.environ["ADMIN_ID"])
DB = os.environ.get("DB_PATH", "/data/shop.db")
PORT = int(os.environ.get("PORT", "8080"))
LABEL = os.environ.get("CUR_LABEL", "R$")
CURRENCY = os.environ.get("CURRENCY", "NGN")
COUNTRY = os.environ.get("COUNTRY", "NG")
GLADE_BASE = os.environ.get("GLADE_BASE", "https://api.glade.ng")
GKEY, GMID = os.environ.get("GLADE_KEY", ""), os.environ.get("GLADE_MID", "")
GSECRET = os.environ.get("GLADE_SECRET", "troque")
SK = os.environ.get("STRIPE_SK", "")

os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
LOCK = threading.RLock()
BOT = LOOP = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT,
  balance REAL DEFAULT 0, created INTEGER);
CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
  delta REAL, reason TEXT, ref TEXT, at INTEGER);
CREATE TABLE IF NOT EXISTS invoices(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
  amount REAL, ref TEXT UNIQUE, status TEXT DEFAULT 'pending', at INTEGER);
CREATE TABLE IF NOT EXISTS bins(bin TEXT PRIMARY KEY, brand TEXT, level TEXT, bank TEXT,
  country TEXT, kind TEXT);
CREATE TABLE IF NOT EXISTS cards(id INTEGER PRIMARY KEY AUTOINCREMENT, num TEXT UNIQUE,
  mm TEXT, yy TEXT, cvv TEXT, holder TEXT, bin TEXT, brand TEXT, level TEXT, price REAL,
  status TEXT DEFAULT 'live', sold_to INTEGER);
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
  card_id INTEGER, price REAL, status TEXT DEFAULT 'paid', at INTEGER);
"""

def q(sql, a=(), one=False):
    with LOCK:
        c = sqlite3.connect(DB, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            r = c.execute(sql, a).fetchall()
            c.commit()
            return (r[0] if r else None) if one else r
        finally:
            c.close()

def money(v): return f"{LABEL} {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def luhn(n):
    if not n.isdigit() or not 12 <= len(n) <= 19: return False
    s, alt = 0, False
    for d in reversed(n):
        v = int(d)
        if alt:
            v *= 2
            if v > 9: v -= 9
        s += v; alt = not alt
    return s % 10 == 0

BRANDS = [(r"^4", "VISA"), (r"^5[1-5]", "MASTERCARD"), (r"^2[2-7]", "MASTERCARD"),
          (r"^3[47]", "AMEX"), (r"^3(0[0-5]|[68])", "DINERS"), (r"^35", "JCB"),
          (r"^6(011|5)", "DISCOVER"), (r"^62", "UNIONPAY"),
          (r"^(4011|4312|4389|5041|5066|5067|5090|6277|6362|6363|650[0-9]|6516|6550)", "ELO")]
LEVELS = {"CLASSIC": "CLASSIC", "STANDARD": "STANDARD", "GOLD": "GOLD", "PLATINUM": "PLATINUM",
          "SIGNATURE": "SIGNATURE", "INFINITE": "INFINITE", "BUSINESS": "BUSINESS",
          "COMMERCIAL": "BUSINESS", "BLACK": "BLACK", "PREPAID": "PREPAID", "STUDENT": "STUDENT"}
DEFAULT_PRICE = {"PREPAID": 3, "STUDENT": 3, "STANDARD": 4, "CLASSIC": 4, "GOLD": 6,
                 "PLATINUM": 9, "SIGNATURE": 12, "BUSINESS": 10, "INFINITE": 18,
                 "BLACK": 20, "UNKNOWN": 5}

def brand_of(b):
    for p, n in BRANDS:
        if re.match(p, b): return n
    return "UNKNOWN"

def lookup(b6):
    b6 = b6[:6]
    r = q("SELECT * FROM bins WHERE bin=?", (b6,), one=True)
    if r: return dict(r)
    d = {"bin": b6, "brand": brand_of(b6), "level": "UNKNOWN", "bank": "?", "country": "?", "kind": "?"}
    try:
        x = requests.get(f"https://binlist.io/lookup/{b6}/", timeout=8,
                         headers={"User-Agent": "shop/1.0"}).json()
        if x.get("success"):
            d.update(brand=x.get("scheme") or d["brand"],
                     level=LEVELS.get((x.get("category") or "").upper(), "UNKNOWN"),
                     bank=(x.get("bank") or {}).get("name") or "?",
                     country=(x.get("country") or {}).get("alpha2") or "?",
                     kind=x.get("type") or "?")
        q("INSERT OR REPLACE INTO bins VALUES(?,?,?,?,?,?)",
          (d["bin"], d["brand"], d["level"], d["bank"], d["country"], d["kind"]))
    except Exception:
        pass
    return d

def bal(uid):
    r = q("SELECT balance FROM users WHERE id=?", (uid,), one=True)
    return r["balance"] if r else 0.0

def credit(uid, delta, reason, ref=""):
    q("INSERT INTO ledger(user_id,delta,reason,ref,at) VALUES(?,?,?,?,?)",
      (uid, delta, reason, ref, int(time.time())))
    q("UPDATE users SET balance=balance+? WHERE id=?", (delta, uid))

def ensure(u):
    q("INSERT OR IGNORE INTO users(id,username,created) VALUES(?,?,?)", (u.id, u.username, int(time.time())))
    q("UPDATE users SET username=? WHERE id=?", (u.username, u.id))

def kb(rows): return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d)] for t, d in rows])
MAIN = lambda: kb([("Comprar", "shop"), ("Saldo", "wallet"), ("Pedidos", "orders"),
                   ("Consultar BIN", "howbin"), ("Checar", "howchk")])

H = lambda: {"key": GKEY, "mid": GMID, "Content-Type": "application/json"}

def gp_create(user, amount):
    ref = f"INV{user.id}-{secrets.token_hex(4)}"
    body = {"action": "initiate", "paymentType": "banktransfer",
            "user": {"firstname": user.first_name or "C", "lastname": user.last_name or "",
                     "email": f"{user.id}@t.me", "ip": "127.0.0.1", "fingerprint": str(user.id)},
            "amount": str(int(round(amount * 100))), "country": COUNTRY,
            "currency": CURRENCY, "merchant_tx_ref": ref}
    r = requests.post(f"{GLADE_BASE}/payment", json=body, headers=H(), timeout=25)
    j = r.json()
    q("INSERT INTO invoices(user_id,amount,ref,at) VALUES(?,?,?,?)", (user.id, amount, ref, int(time.time())))
    return ref, j

def gp_verify(ref):
    try:
        r = requests.get(f"{GLADE_BASE}/payment/verify", params={"txnRef": ref}, headers=H(), timeout=20)
        return (r.json().get("txnStatus") or "").lower() == "successful"
    except Exception:
        return False

def settle(ref):
    inv = q("SELECT * FROM invoices WHERE ref=? AND status='pending'", (ref,), one=True)
    if not inv or not gp_verify(ref): return
    q("UPDATE invoices SET status='paid' WHERE id=?", (inv["id"],))
    credit(inv["user_id"], inv["amount"], "recarga", ref)
    if LOOP:
        asyncio.run_coroutine_threadsafe(
            BOT.send_message(inv["user_id"], f"Creditado {money(inv['amount'])}."), LOOP)

webapp = Flask(__name__)

@webapp.route("/")
def health(): return "up", 200

@webapp.route("/hook/<secret>", methods=["POST", "GET"])
def hook(secret):
    if secret != GSECRET: return "forbidden", 403
    d = freq.get_json(force=True, silent=True) or {}
    ref = d.get("txnRef") or d.get("merchant_tx_ref") or freq.args.get("txnRef")
    if ref: threading.Thread(target=settle, args=(ref,), daemon=True).start()
    return "ok", 200

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    ensure(u.effective_user)
    await u.message.reply_text(f"Saldo: {money(bal(u.effective_user.id))}", reply_markup=MAIN())

async def home(u: Update, c: ContextTypes.DEFAULT_TYPE):
    qr = u.callback_query; await qr.answer()
    await qr.edit_message_text(f"Saldo: {money(bal(qr.from_user.id))}", reply_markup=MAIN())

async def howbin(u: Update, c: ContextTypes.DEFAULT_TYPE):
    qr = u.callback_query; await qr.answer()
    await qr.edit_message_text("<code>/bin 532819</code>", parse_mode="HTML",
                               reply_markup=kb([("Menu", "home")]))

async def cmd_bin(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not c.args or not c.args[0].isdigit() or len(c.args[0]) < 6:
        return await u.message.reply_text("Uso: /bin 532819")
    d = await asyncio.to_thread(lookup, c.args[0])
    n = q("SELECT COUNT(*) n, MIN(price) p FROM cards WHERE bin=? AND status='live'", (d["bin"],), one=True)
    await u.message.reply_text(
        f"<b>BIN {d['bin']}</b>\nBandeira: <b>{d['brand']}</b>\nNivel: <b>{d['level']}</b>\n"
        f"Tipo: {d['kind']}\nBanco: {d['bank']}\nPais: {d['country']}\n\n"
        f"Estoque: {n['n']}\nPreco unitario: {money(n['p']) if n['p'] else 'n/d'}",
        parse_mode="HTML",
        reply_markup=kb([("Comprar 1 deste BIN", f"buyb:{d['bin']}")]) if n["n"] else None)

async def wallet(u: Update, c: ContextTypes.DEFAULT_TYPE):
    qr = u.callback_query; await qr.answer()
    await qr.edit_message_text(f"Saldo: {money(bal(qr.from_user.id))}\nRecarregar:",
        reply_markup=kb([(money(v), f"top:{v}") for v in (5, 10, 25, 50)] + [("Voltar", "home")]))

async def top(u: Update, c: ContextTypes.DEFAULT_TYPE):
    qr = u.callback_query; await qr.answer()
    v = float(qr.data.split(":")[1])
    ref, j = await asyncio.to_thread(gp_create, qr.from_user, v)
    bt = j.get("bankTransfer") or j.get("banktransfer") or {}
    acct = bt.get("accountNumber") or bt.get("accountnumber") or "-"
    bank = bt.get("bankName") or bt.get("bankname") or "-"
    await qr.edit_message_text(
        f"Recarga {money(v)}\n\nBanco: <b>{bank}</b>\nConta: <code>{acct}</code>\n"
        f"Ref: <code>{ref}</code>\n\nPague o valor exato.",
        parse_mode="HTML", reply_markup=kb([("Menu", "home")]))

async def shop(u: Update, c: ContextTypes.DEFAULT_TYPE):
    qr = u.callback_query; await qr.answer()
    rows = q("SELECT level, COUNT(*) n, MIN(price) p FROM cards WHERE status='live' GROUP BY level ORDER BY p")
    if not rows: return await qr.edit_message_text("Sem stock.", reply_markup=MAIN())
    await qr.edit_message_text("Nivel:", reply_markup=kb(
        [(f"{r['level']} — {money(r['p'])} | {r['n']}", f"br:{r['level']}") for r in rows] + [("Voltar", "home")]))

async def by_brand(u: Update, c: ContextTypes.DEFAULT_TYPE):
    qr = u.callback_query; await qr.answer()
    lv = qr.data.split(":", 1)[1]
    rows = q("SELECT brand, COUNT(*) n, MIN(price) p FROM cards WHERE level=? AND status='live' "
             "GROUP BY brand ORDER BY n DESC", (lv,))
    if not rows: return await qr.answer("Esgotou.", show_alert=True)
    await qr.edit_message_text(f"{lv}\nBandeira:", reply_markup=kb(
        [(f"{r['brand']} — {money(r['p'])} | {r['n']}", f"bn:{lv}:{r['brand']}") for r in rows]
        + [("Voltar", "shop")]))

async def by_bin(u: Update, c: ContextTypes.DEFAULT_TYPE):
    qr = u.callback_query; await qr.answer()
    _, lv, br = qr.data.split(":", 2)
    rows = q("SELECT bin, COUNT(*) n, MIN(price) p FROM cards WHERE level=? AND brand=? AND status='live' "
             "GROUP BY bin ORDER BY p", (lv, br))
    if not rows: return await qr.answer("Esgotou.", show_alert=True)
    await qr.edit_message_text(f"{lv} / {br}\nBIN:", reply_markup=kb(
        [(f"{r['bin']} — {money(r['p'])} | {r['n']} un", f"buyb:{r['bin']}") for r in rows]
        + [("Voltar", f"br:{lv}")]))

async def deliver_card(card, uid, reply):
    o = q("INSERT INTO orders(user_id,card_id,price,at) VALUES(?,?,?,?) RETURNING id",
          (uid, card["id"], card["price"], int(time.time())), one=True)
    q("UPDATE cards SET status='sold', sold_to=? WHERE id=?", (uid, card["id"]))
    credit(uid, -card["price"], "compra", str(o["id"]))
    await reply(f"Pedido #{o['id']} — 1 unidade\n\n"
                f"<code>{card['num']}|{card['mm']}|{card['yy']}|{card['cvv']}</code>\n"
                f"Titular: {card['holder'] or '-'}\n"
                f"{card['brand']} {card['level']} {card['bin']}\nSaldo: {money(bal(uid))}",
                parse_mode="HTML",
                reply_markup=kb([(f"Morto #{o['id']}", f"dead:{o['id']}"), ("Menu", "home")]))

async def buy_bin(u: Update, c: ContextTypes.DEFAULT_TYPE):
    qr = u.callback_query; await qr.answer()
    b6 = qr.data.split(":", 1)[1]
    card = q("SELECT * FROM cards WHERE bin=? AND status='live' ORDER BY price, id LIMIT 1", (b6,), one=True)
    if not card: return await qr.answer("Esgotou.", show_alert=True)
    b = bal(qr.from_user.id)
    if b < card["price"]:
        return await qr.answer(f"Saldo insuficiente. Faltam {money(card['price'] - b)}", show_alert=True)
    await deliver_card(card, qr.from_user.id, qr.message.reply_text)

async def dead(u: Update, c: ContextTypes.DEFAULT_TYPE):
    qr = u.callback_query; await qr.answer()
    oid = int(qr.data.split(":")[1])
    o = q("SELECT * FROM orders WHERE id=? AND user_id=?", (oid, qr.from_user.id), one=True)
    if not o or o["status"] != "paid": return await qr.answer("Invalido.", show_alert=True)
    old = q("SELECT * FROM cards WHERE id=?", (o["card_id"],), one=True)
    q("UPDATE cards SET status='dead' WHERE id=?", (old["id"],))
    n = q("SELECT * FROM cards WHERE status='live' AND bin=? AND id<>? ORDER BY price LIMIT 1",
          (old["bin"], old["id"]), one=True)
    if n:
        q("UPDATE cards SET status='sold', sold_to=? WHERE id=?", (qr.from_user.id, n["id"]))
        q("UPDATE orders SET card_id=? WHERE id=?", (n["id"], oid))
        return await qr.message.reply_text(
            f"Substituido:\n<code>{n['num']}|{n['mm']}|{n['yy']}|{n['cvv']}</code>", parse_mode="HTML")
    q("UPDATE orders SET status='refunded' WHERE id=?", (oid,))
    credit(qr.from_user.id, o["price"], "reembolso", str(oid))
    await qr.message.reply_text(f"Sem stock do BIN. Reembolsado {money(o['price'])}.")

async def orders(u: Update, c: ContextTypes.DEFAULT_TYPE):
    qr = u.callback_query; await qr.answer()
    rows = q("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (qr.from_user.id,))
    await qr.edit_message_text("\n".join(f"#{r['id']} {money(r['price'])} {r['status']}" for r in rows) or "Nenhum.",
                               reply_markup=kb([("Menu", "home")]))

async def howchk(u: Update, c: ContextTypes.DEFAULT_TYPE):
    qr = u.callback_query; await qr.answer()
    await qr.edit_message_text("<code>/chk num|mm|yy|cvv</code>", parse_mode="HTML",
                               reply_markup=kb([("Menu", "home")]))

async def ingest(u, raw):
    if u.effective_user.id != ADMIN: return
    ok = bad = 0
    for line in raw.splitlines():
        f = [x.strip() for x in re.split(r"[|;,\t]", line) if x.strip()]
        if len(f) < 4: continue
        num = re.sub(r"\D", "", f[0])
        if not luhn(num): bad += 1; continue
        d = await asyncio.to_thread(lookup, num)
        price = float(f[6]) if len(f) > 6 else DEFAULT_PRICE.get(d["level"], 5.0)
        try:
            q("INSERT INTO cards(num,mm,yy,cvv,holder,bin,brand,level,price) VALUES(?,?,?,?,?,?,?,?,?)",
              (num, f[1].zfill(2), f[2][-2:], f[3], f[4] if len(f) > 4 else None,
               d["bin"], d["brand"], d["level"], price))
            ok += 1
        except Exception: bad += 1
    await u.message.reply_text(f"Inseridos {ok} | invalidos/dup {bad}")

async def addcc(u: Update, c: ContextTypes.DEFAULT_TYPE):
    raw = u.message.reply_to_message.text if u.message.reply_to_message else u.message.text.split(None, 1)[-1]
    await ingest(u, raw)

async def addcc_file(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN: return
    f = await u.message.document.get_file()
    raw = (await f.download_as_bytearray()).decode("utf-8", "ignore")
    await ingest(u, raw)

async def stockcmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN: return
    rows = q("SELECT status, COUNT(*) n FROM cards GROUP BY status")
    await u.message.reply_text("\n".join(f"{r['status']}: {r['n']}" for r in rows) or "vazio")

async def stats(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN: return
    v = q("SELECT COUNT(*), COALESCE(SUM(price),0) FROM orders", one=True)
    await u.message.reply_text(f"Vendas {v[0]} | Total {money(v[1])}")

async def saldo(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN or len(c.args) < 2: return
    uid, v = int(c.args[0]), float(c.args[1].replace(",", "."))
    credit(uid, v, "manual", "admin")
    await u.message.reply_text(f"{uid} agora tem {money(bal(uid))}")

async def chk_entry(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if c.args: await run_chk(u, c.args[0]); return ConversationHandler.END
    await u.message.reply_text("Manda: num|mm|yy|cvv"); return 1

async def chk_in(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await run_chk(u, u.message.text); return ConversationHandler.END

async def run_chk(u, raw):
    f = [x.strip() for x in raw.replace(";", "|").split("|")]
    if len(f) < 4: return await u.message.reply_text("Formato: num|mm|yy|cvv")
    if not luhn(f[0].replace(" ", "")): return await u.message.reply_text("Luhn invalido.")
    if not SK: return await u.message.reply_text("CHK desligado (falta STRIPE_SK).")
    m = await u.message.reply_text("Checando...")
    d = {"amount": 100, "currency": "brl", "capture_method": "manual", "confirm": "true",
         "error_on_requires_action": "true", "payment_method_data[type]": "card",
         "payment_method_data[card][number]": f[0].replace(" ", ""),
         "payment_method_data[card][exp_month]": f[1], "payment_method_data[card][exp_year]": f[2],
         "payment_method_data[card][cvc]": f[3]}
    r = await asyncio.to_thread(lambda: requests.post(
        "https://api.stripe.com/v1/payment_intents", data=d, auth=(SK, ""), timeout=25))
    j = r.json()
    if j.get("status") in ("succeeded", "requires_capture"):
        await asyncio.to_thread(lambda: requests.post(
            f"https://api.stripe.com/v1/payment_intents/{j['id']}/cancel", auth=(SK, ""), timeout=15))
        return await m.edit_text("<b>APROVADO</b>", parse_mode="HTML")
    e = j.get("error") or {}
    await m.edit_text(f"<b>RECUSADO</b> ({e.get('decline_code') or e.get('code') or '?'})", parse_mode="HTML")

async def on_start(app):
    global BOT, LOOP
    BOT, LOOP = app.bot, asyncio.get_running_loop()

def main():
    c = sqlite3.connect(DB); c.executescript(SCHEMA); c.commit(); c.close()
    app = Application.builder().token(TOKEN).post_init(on_start).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("bin", cmd_bin))
    app.add_handler(CommandHandler("addcc", addcc))
    app.add_handler(MessageHandler(filters.Document.ALL, addcc_file))
    app.add_handler(CommandHandler("stock", stockcmd))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("saldo", saldo))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("chk", chk_entry)],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, chk_in)]}, fallbacks=[]))
    for n, f in [("shop", shop), ("home", home), ("wallet", wallet), ("orders", orders),
                 ("howbin", howbin), ("howchk", howchk)]:
        app.add_handler(CallbackQueryHandler(f, pattern=f"^{n}$"))
    app.add_handler(CallbackQueryHandler(top, pattern="^top:"))
    app.add_handler(CallbackQueryHandler(by_brand, pattern="^br:"))
    app.add_handler(CallbackQueryHandler(by_bin, pattern="^bn:"))
    app.add_handler(CallbackQueryHandler(buy_bin, pattern="^buyb:"))
    app.add_handler(CallbackQueryHandler(dead, pattern="^dead:"))
    threading.Thread(target=lambda: webapp.run("0.0.0.0", PORT, threaded=True), daemon=True).start()
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
