import asyncio, os, re, time, sqlite3, secrets, threading, logging
import requests
from dotenv import load_dotenv
from flask import Flask, request as flask_request

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, ConversationHandler, MessageHandler, filters)

load_dotenv()
logging.basicConfig(level=logging.INFO)

TOKEN     = os.environ["BOT_TOKEN"]
ADMIN_ID  = int(os.environ["ADMIN_ID"])
DB        = os.environ.get("DB_PATH", "store.db")
WARRANTY  = int(os.environ.get("WARRANTY_MIN", "180"))
PORT      = int(os.environ.get("PORT", "8080"))

GLADE_BASE   = os.environ.get("GLADE_BASE", "https://api.glade.ng")
GLADE_KEY    = os.environ.get("GLADE_KEY", "")
GLADE_MID    = os.environ.get("GLADE_MID", "")
GLADE_SECRET = os.environ.get("GLADE_SECRET", "troque")
CURRENCY     = os.environ.get("CURRENCY", "NGN")
COUNTRY      = os.environ.get("COUNTRY", "NG")
CUR_LABEL    = os.environ.get("CUR_LABEL", "R$")

STRIPE_SK = os.environ.get("STRIPE_SK", "")
CHK_AMOUNT = int(os.environ.get("CHK_AMOUNT", "100"))

LOCK = threading.Lock()
BOT = None
LOOP = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS bins(bin TEXT PRIMARY KEY, brand TEXT, level TEXT, bank TEXT,
  country TEXT, kind TEXT, at INTEGER);
CREATE TABLE IF NOT EXISTS prices(level TEXT PRIMARY KEY, price REAL NOT NULL);
CREATE TABLE IF NOT EXISTS stock(id INTEGER PRIMARY KEY, num TEXT UNIQUE, mm TEXT, yy TEXT,
  cvv TEXT, holder TEXT, cpf TEXT, bin TEXT, brand TEXT, level TEXT, price REAL,
  status TEXT DEFAULT 'available', added_by INTEGER, added_at INTEGER, sold_at INTEGER,
  order_id INTEGER);
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
  stock_id INTEGER, amount REAL, status TEXT DEFAULT 'pending', ref TEXT, at INTEGER,
  warranty_until INTEGER);
CREATE TABLE IF NOT EXISTS chk(id INTEGER PRIMARY KEY, user_id INTEGER, num TEXT, code TEXT, at INTEGER);
"""

def db():
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with LOCK:
        c = db()
        c.executescript(SCHEMA)
        if not c.execute("SELECT 1 FROM prices LIMIT 1").fetchone():
            c.executemany("INSERT INTO prices VALUES(?,?)", [
                ("STANDARD", 8), ("CLASSIC", 8), ("GOLD", 12), ("PLATINUM", 18),
                ("SIGNATURE", 25), ("INFINITE", 35), ("BUSINESS", 20), ("BLACK", 40),
                ("PREPAID", 6), ("STUDENT", 6), ("UNKNOWN", 10)])
        c.commit(); c.close()

def brl(v):
    return f"{CUR_LABEL} {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d)] for t, d in rows])

MAIN = lambda: kb([("Comprar", "shop"), ("Consultar BIN", "howbin"),
                   ("Checar CC", "howchk"), ("Meus pedidos", "pedidos")])

# ---------- BIN ----------
BRANDS = [(r'^4', 'VISA'), (r'^5[1-5]', 'MASTERCARD'), (r'^2[2-7]', 'MASTERCARD'),
          (r'^3[47]', 'AMEX'), (r'^3(0[0-5]|[68])', 'DINERS'), (r'^35', 'JCB'),
          (r'^6(011|5|4[4-9])', 'DISCOVER'), (r'^62', 'UNIONPAY'),
          (r'^(4011|4312|4389|5041|5066|5067|5090|6277|6362|6363|650[0-9]|6516|6550)', 'ELO'),
          (r'^606282', 'HIPERCARD')]
LEVELS = {'CLASSIC': 'CLASSIC', 'STANDARD': 'STANDARD', 'GOLD': 'GOLD', 'PLATINUM': 'PLATINUM',
          'SIGNATURE': 'SIGNATURE', 'INFINITE': 'INFINITE', 'BUSINESS': 'BUSINESS',
          'COMMERCIAL': 'BUSINESS', 'BLACK': 'BLACK', 'PREPAID': 'PREPAID', 'STUDENT': 'STUDENT'}

def luhn(n):
    if not n.isdigit() or not 12 <= len(n) <= 19:
        return False
    s, alt = 0, False
    for d in reversed(n):
        v = int(d)
        if alt:
            v *= 2
            if v > 9: v -= 9
        s += v; alt = not alt
    return s % 10 == 0

def brand_of(b6):
    for p, name in BRANDS:
        if re.match(p, b6):
            return name
    return 'UNKNOWN'

def lookup(num):
    b6 = num[:6]
    c = db()
    row = c.execute("SELECT * FROM bins WHERE bin=?", (b6,)).fetchone()
    c.close()
    if row:
        return dict(row)
    d = {'bin': b6, 'brand': brand_of(b6), 'level': 'UNKNOWN', 'bank': '?',
         'country': '?', 'kind': '?'}
    try:
        r = requests.get(f"https://binlist.io/lookup/{b6}/", timeout=8,
                         headers={"User-Agent": "store-bot/1.0"})
        if r.status_code == 200:
            j = r.json()
            if j.get("success"):
                d.update(brand=j.get("scheme") or d["brand"],
                         level=LEVELS.get((j.get("category") or "").upper(), "UNKNOWN"),
                         bank=(j.get("bank") or {}).get("name") or "?",
                         country=(j.get("country") or {}).get("alpha2") or "?",
                         kind=j.get("type") or "?")
                with LOCK:
                    c = db()
                    c.execute("INSERT OR REPLACE INTO bins VALUES(?,?,?,?,?,?,?)",
                              (b6, d["brand"], d["level"], d["bank"], d["country"],
                               d["kind"], int(time.time())))
                    c.commit(); c.close()
    except Exception as e:
        logging.warning("bin %s: %s", b6, e)
    return d

# ---------- CHK (Stripe) ----------
CODES = {
 'authentication_required': '3DS REQUERIDO', 'authentication_not_handled': '3DS NAO TRATADO',
 'insufficient_funds': 'SALDO INSUFICIENTE', 'withdrawal_count_limit_exceeded': 'SALDO INSUFICIENTE',
 'card_velocity_exceeded': 'LIMITE EXCEDIDO', 'do_not_honor': 'RECUSADO (DO NOT HONOR)',
 'generic_decline': 'RECUSADO (GENERICO)', 'fraudulent': 'RECUSADO (FRAUDE)',
 'expired_card': 'CARTAO EXPIRADO', 'incorrect_cvc': 'CVV INCORRETO', 'invalid_cvc': 'CVV INCORRETO',
 'incorrect_number': 'NUMERO INVALIDO', 'invalid_number': 'NUMERO INVALIDO',
 'incorrect_zip': 'CEP INCORRETO', 'incorrect_address': 'ENDERECO INCORRETO',
 'lost_card': 'CARTAO PERDIDO', 'stolen_card': 'CARTAO ROUBADO', 'pickup_card': 'CAPTURAR CARTAO',
 'restricted_card': 'CARTAO RESTRITO', 'transaction_not_allowed': 'TRANSACAO NAO PERMITIDA',
 'card_not_supported': 'CARTAO NAO SUPORTADO', 'currency_not_supported': 'MOEDA NAO SUPORTADA',
 'call_issuer': 'CHAMAR EMISSOR', 'processing_error': 'ERRO DE PROCESSAMENTO',
 'issuer_not_available': 'EMISSOR INDISPONIVEL', 'try_again_later': 'TENTAR NOVAMENTE',
 'reenter_transaction': 'TENTAR NOVAMENTE', 'approve_with_id': 'TENTAR NOVAMENTE',
}

def do_chk(num, mm, yy, cvv, holder=""):
    if not STRIPE_SK:
        return "CHK DESLIGADO (sem STRIPE_SK)"
    payload = {
        "amount": CHK_AMOUNT, "currency": "brl", "capture_method": "manual",
        "confirm": "true", "error_on_requires_action": "true",
        "payment_method_data[type]": "card",
        "payment_method_data[card][number]": num,
        "payment_method_data[card][exp_month]": mm,
        "payment_method_data[card][exp_year]": yy,
        "payment_method_data[card][cvc]": cvv,
        "payment_method_data[billing_details][name]": holder or "CHK",
    }
    r = requests.post("https://api.stripe.com/v1/payment_intents", data=payload,
                      auth=(STRIPE_SK, ""), timeout=25)
    j = r.json()
    if j.get("status") in ("succeeded", "requires_capture"):
        requests.post(f"https://api.stripe.com/v1/payment_intents/{j['id']}/cancel",
                      auth=(STRIPE_SK, ""), timeout=15)
        return "APROVADO"
    err = j.get("error") or {}
    code = err.get("decline_code") or err.get("code") or "generic_decline"
    return CODES.get(code, f"RECUSADO ({code})")

# ---------- GladePay ----------
def glade_headers():
    return {"key": GLADE_KEY, "mid": GLADE_MID, "Content-Type": "application/json"}

def glade_create(order_id, amount, user):
    ref = f"ORD{order_id}-{secrets.token_hex(3)}"
    body = {
        "action": "initiate", "paymentType": "banktransfer",
        "user": {"firstname": user.first_name or "Cliente", "lastname": user.last_name or "",
                 "email": f"{user.id}@t.me", "ip": "127.0.0.1", "fingerprint": str(user.id)},
        "amount": str(int(round(amount * 100))), "country": COUNTRY,
        "currency": CURRENCY, "merchant_tx_ref": ref,
    }
    r = requests.post(f"{GLADE_BASE}/payment", json=body, headers=glade_headers(), timeout=25)
    j = r.json()
    return ref, j

def glade_verify(ref):
    try:
        r = requests.get(f"{GLADE_BASE}/payment/verify", params={"txnRef": ref},
                         headers=glade_headers(), timeout=20)
        j = r.json()
        return (j.get("txnStatus") or j.get("status") or "").lower() == "successful", j
    except Exception as e:
        logging.warning("verify %s: %s", ref, e)
        return False, {}

# ---------- entrega ----------
def deliver(order_id):
    with LOCK:
        c = db()
        o = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not o or o["status"] == "paid":
            c.close(); return
        s = c.execute("SELECT * FROM stock WHERE id=?", (o["stock_id"],)).fetchone()
        now = int(time.time())
        c.execute("UPDATE stock SET status='sold', sold_at=? WHERE id=?", (now, s["id"]))
        c.execute("UPDATE orders SET status='paid', warranty_until=? WHERE id=?",
                  (now + WARRANTY * 60, order_id))
        c.commit(); c.close()
    until = time.strftime("%d/%m %H:%M", time.localtime(now + WARRANTY * 60))
    txt = (f"Pago #{order_id}\n\n*{s['brand']} {s['level']}*\n"
           f"`{s['num']}|{s['mm']}|{s['yy']}|{s['cvv']}`\n"
           f"Titular: {s['holder'] or '-'} | CPF: {s['cpf'] or '-'}\n\n"
           f"Garantia ate {until}. /troca para troca.")
    if LOOP:
        asyncio.run_coroutine_threadsafe(
            BOT.send_message(o["user_id"], txt, parse_mode="Markdown"), LOOP)

def check_and_deliver(ref):
    ok, j = glade_verify(ref)
    if not ok:
        return
    with LOCK:
        c = db()
        o = c.execute("SELECT id FROM orders WHERE ref=?", (ref,)).fetchone()
        c.close()
    if o:
        deliver(o["id"])

# ---------- Flask ----------
flask_app = Flask(__name__)

@flask_app.route("/hook/<secret>", methods=["POST", "GET"])
def hook(secret):
    if secret != GLADE_SECRET:
        return "forbidden", 403
    d = flask_request.get_json(force=True, silent=True) or {}
    ref = d.get("txnRef") or d.get("merchant_tx_ref") or flask_request.args.get("txnRef")
    if ref:
        threading.Thread(target=check_and_deliver, args=(ref,), daemon=True).start()
    return "ok", 200

@flask_app.route("/")
def health():
    return "up", 200

# ---------- handlers ----------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("*Store*\nEscolha:", reply_markup=MAIN(), parse_mode="Markdown")

async def howbin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await q.edit_message_text("Use `/bin 532819`", reply_markup=MAIN(), parse_mode="Markdown")

async def howchk(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await q.edit_message_text("Use `/chk 5328190835630073|12|27|356`\n"
                              "Cada chk autoriza R$ 1,00 e segura saldo em pre-pago.",
                              reply_markup=MAIN(), parse_mode="Markdown")

async def home(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await q.edit_message_text("*Store*", reply_markup=MAIN(), parse_mode="Markdown")

async def cmd_bin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or not ctx.args[0].isdigit() or len(ctx.args[0]) < 6:
        return await update.message.reply_text("Uso: /bin 532819")
    arg = ctx.args[0]
    d = await asyncio.to_thread(lookup, arg)
    c = db()
    n = c.execute("SELECT COUNT(*) FROM stock WHERE bin=? AND status='available'", (d["bin"],)).fetchone()[0]
    p = c.execute("SELECT price FROM prices WHERE level=?", (d["level"],)).fetchone()
    c.close()
    txt = (f"*BIN {d['bin']}*\nBandeira: {d['brand']}\nNivel: *{d['level']}*\n"
           f"Tipo: {d['kind']}\nBanco: {d['bank']}\nPais: {d['country']}\n"
           f"Estoque: {n}\nPreco: {brl(p['price']) if p else 'n/d'}")
    markup = kb([("Comprar", f"buy:{d['level']}")]) if n else None
    await update.message.reply_text(txt, reply_markup=markup, parse_mode="Markdown")

async def cmd_addcc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = update.message
    if m.from_user.id != ADMIN_ID:
        return await m.reply_text("So admin.")
    src = m.reply_to_message.text if m.reply_to_message else m.text.split(None, 1)[-1]
    lines = [l.strip() for l in src.splitlines() if l.strip() and not l.startswith("/")]
    ok = dup = bad = 0; by = {}
    for l in lines:
        f = [x.strip() for x in l.replace(";", "|").split("|")]
        if len(f) < 4:
            bad += 1; continue
        num, mm, yy, cvv = f[0].replace(" ", ""), f[1], f[2], f[3]
        holder = f[4] if len(f) > 4 else ""
        cpf = f[5] if len(f) > 5 else ""
        if not luhn(num):
            bad += 1; continue
        d = await asyncio.to_thread(lookup, num)
        c = db()
        p = c.execute("SELECT price FROM prices WHERE level=?", (d["level"],)).fetchone()
        try:
            c.execute("INSERT INTO stock(num,mm,yy,cvv,holder,cpf,bin,brand,level,price,added_by,added_at)"
                      " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (num, mm, yy, cvv, holder, cpf, d["bin"], d["brand"], d["level"],
                       p["price"] if p else 10, m.from_user.id, int(time.time())))
            c.commit(); ok += 1; by[d["level"]] = by.get(d["level"], 0) + 1
        except sqlite3.IntegrityError:
            dup += 1
        finally:
            c.close()
    det = "\n".join(f"  {k}: {v}" for k, v in sorted(by.items()))
    await m.reply_text(f"OK {ok} | dup {dup} | invalidos {bad}" + (f"\n\n{det}" if det else ""))

async def chk_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = update.message
    src = m.reply_to_message.text if m.reply_to_message else (m.text.split(None, 1) + [""])[1]
    if src.strip():
        await run_chk(m, src)
        return ConversationHandler.END
    await m.reply_text("Manda: numero|mm|yy|cvv")
    return 1

async def chk_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await run_chk(update.message, update.message.text)
    return ConversationHandler.END

async def run_chk(m, raw):
    f = [x.strip() for x in raw.replace(";", "|").split("|")]
    if len(f) < 4:
        return await m.reply_text("Formato: numero|mm|yy|cvv")
    num, mm, yy, cvv = f[0].replace(" ", ""), f[1].zfill(2), f[2], f[3]
    if not luhn(num):
        return await m.reply_text("Luhn invalido.")
    d = await asyncio.to_thread(lookup, num)
    c = db()
    n = c.execute("SELECT COUNT(*) FROM chk WHERE user_id=? AND at>?",
                  (m.from_user.id, int(time.time()) - 60)).fetchone()[0]
    c.close()
    if n > 10:
        return await m.reply_text("10 chk por minuto, calma.")
    msg = await m.reply_text("Checando...")
    label = await asyncio.to_thread(do_chk, num, mm, yy, cvv, f[4] if len(f) > 4 else "")
    c = db()
    c.execute("INSERT INTO chk(user_id,num,code,at) VALUES(?,?,?,?)",
              (m.from_user.id, num, label, int(time.time())))
    c.commit(); c.close()
    await msg.edit_text(f"*{label}*\n\n{num[:6]}****{num[-4:]}\n"
                        f"{d['brand']} | {d['level']} | {d['bank']} | {d['country']}",
                        parse_mode="Markdown")

async def shop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    c = db()
    rows = c.execute("SELECT level, COUNT(*) n, MIN(price) p FROM stock WHERE status='available' "
                     "GROUP BY level ORDER BY p").fetchall()
    c.close()
    if not rows:
        return await q.edit_message_text("Sem estoque.", reply_markup=MAIN())
    markup = kb([(f"{x['level']} — {brl(x['p'])} | {x['n']} un", f"buy:{x['level']}") for x in rows]
                + [("Voltar", "home")])
    await q.edit_message_text("Escolha o nivel:", reply_markup=markup)

async def buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    lv = q.data.split(":", 1)[1]
    with LOCK:
        c = db()
        s = c.execute("SELECT * FROM stock WHERE level=? AND status='available' ORDER BY id LIMIT 1",
                      (lv,)).fetchone()
        if not s:
            c.close(); return await q.answer("Esgotou.", show_alert=True)
        cur = c.execute("INSERT INTO orders(user_id,stock_id,amount,at) VALUES(?,?,?,?)",
                        (q.from_user.id, s["id"], s["price"], int(time.time())))
        oid = cur.lastrowid
        c.execute("UPDATE stock SET status='reserved', order_id=? WHERE id=?", (oid, s["id"]))
        c.commit(); c.close()
    await q.edit_message_text(f"*Pedido #{oid}*\n{lv} — {brl(s['price'])}",
                              reply_markup=kb([("Pagar", f"pay:{oid}"), ("Cancelar", "home")]),
                              parse_mode="Markdown")

async def pay(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    oid = int(q.data.split(":")[1])
    c = db(); o = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone(); c.close()
    if not o or o["status"] != "pending":
        return await q.edit_message_text("Ja processado.")
    ref, j = await asyncio.to_thread(glade_create, oid, o["amount"], q.from_user)
    bt = j.get("bankTransfer") or j.get("banktransfer") or {}
    acct = bt.get("accountNumber") or bt.get("accountnumber") or j.get("accountNumber") or "-"
    bank = bt.get("bankName") or bt.get("bankname") or j.get("bankName") or "-"
    c = db(); c.execute("UPDATE orders SET ref=? WHERE id=?", (ref, oid)); c.commit(); c.close()
    link = j.get("redirectUrl") or j.get("link") or ""
    txt = (f"*Pedido #{oid}* — {brl(o['amount'])}\n\nBanco: {bank}\nConta: `{acct}`\n"
           f"Ref: `{ref}`\n\nPague o valor exato. Entrega automatica.")
    if link:
        txt += f"\n\n[Pagar]({link})"
    await q.edit_message_text(txt, parse_mode="Markdown",
                              reply_markup=kb([("Menu", "home")]))

async def pedidos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    c = db()
    rows = c.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (uid,)).fetchall()
    c.close()
    txt = "\n".join(f"#{x['id']} {brl(x['amount'])} {x['status']}" for x in rows) or "Nenhum pedido."
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(txt)
    else:
        await update.message.reply_text(txt)

async def troca(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    c = db()
    o = c.execute("SELECT * FROM orders WHERE user_id=? AND status='paid' ORDER BY id DESC LIMIT 1",
                  (uid,)).fetchone()
    c.close()
    if not o:
        return await update.message.reply_text("Nenhum pedido elegivel.")
    await update.message.reply_text(f"Troca do #{o['id']}. Mande video unico, sem cortes.")
    await BOT.send_message(ADMIN_ID, f"Troca #{o['id']} de {uid}")

async def cmd_preco(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if len(ctx.args) < 2:
        return await update.message.reply_text("Uso: /preco PLATINUM 18")
    lv, v = ctx.args[0].upper(), float(ctx.args[1].replace(",", "."))
    c = db(); c.execute("INSERT OR REPLACE INTO prices VALUES(?,?)", (lv, v)); c.commit(); c.close()
    await update.message.reply_text(f"{lv} = {brl(v)}")

async def cmd_estoque(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    c = db()
    rows = c.execute("SELECT level, COUNT(*) n FROM stock WHERE status='available' GROUP BY level").fetchall()
    c.close()
    await update.message.reply_text("\n".join(f"{x['level']}: {x['n']}" for x in rows) or "vazio")

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    c = db()
    v = c.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM orders WHERE status='paid'").fetchone()
    q = c.execute("SELECT COUNT(*) FROM chk WHERE at>?", (int(time.time()) - 86400,)).fetchone()[0]
    c.close()
    await update.message.reply_text(f"Vendas {v[0]} | Fat {brl(v[1])} | chk 24h {q}")

# ---------- garantia ----------
def warranty_worker():
    while True:
        now = int(time.time())
        c = db()
        for o in c.execute("SELECT * FROM orders WHERE status='paid' AND warranty_until BETWEEN ? AND ?",
                           (now, now + 60)).fetchall():
            c.execute("UPDATE orders SET status='garantia_expirada' WHERE id=?", (o["id"],))
            if LOOP:
                asyncio.run_coroutine_threadsafe(
                    BOT.send_message(o["user_id"], f"Garantia do #{o['id']} encerrada."), LOOP)
        c.commit(); c.close()
        time.sleep(30)

def main():
    global BOT, LOOP
    init_db()
    init_gifts()
    app = Application.builder().token(TOKEN).build()
    BOT = app.bot
    LOOP = asyncio.get_event_loop()

    threading.Thread(target=lambda: flask_app.run("0.0.0.0", PORT), daemon=True).start()
    threading.Thread(target=warranty_worker, daemon=True).start()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("bin", cmd_bin))
    app.add_handler(CommandHandler("addcc", cmd_addcc))
    app.add_handler(CommandHandler("pedidos", pedidos))
    app.add_handler(CommandHandler("troca", troca))
    app.add_handler(CommandHandler("preco", cmd_preco))
    app.add_handler(CommandHandler("estoque", cmd_estoque))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("gerar", gerar))
    app.add_handler(CommandHandler("resgatar", resgatar))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("chk", chk_entry)],
        states={1: [MessageHandler(filters.TEXT & ~filters.COMMAND, chk_input)]},
        fallbacks=[]))
    app.add_handler(CallbackQueryHandler(shop, pattern="^shop$"))
    app.add_handler(CallbackQueryHandler(howbin, pattern="^howbin$"))
    app.add_handler(CallbackQueryHandler(howchk, pattern="^howchk$"))
    app.add_handler(CallbackQueryHandler(home, pattern="^home$"))
    app.add_handler(CallbackQueryHandler(pedidos, pattern="^pedidos$"))
    app.add_handler(CallbackQueryHandler(buy, pattern="^buy:"))
    app.add_handler(CallbackQueryHandler(pay, pattern="^pay:"))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
