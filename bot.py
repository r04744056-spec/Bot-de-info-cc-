import os
import uuid
import sqlite3
import threading
import requests
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

load_dotenv()

# ===== VARIÁVEIS - VEM DO RAILWAY, NÃO ESTÁ ESCRITO AQUI =====
TOKEN = os.getenv("TELEGRAM_TOKEN")
GLADE_API_KEY = os.getenv("GLADE_API_KEY")
BASE_URL = os.getenv("GLADE_BASE_URL")
CALLBACK_URL = os.getenv("CALLBACK_URL")
ENDPOINT_CRIAR_PIX = "/deposits"
# ============================================================

# ===== BANCO =====
conn = sqlite3.connect("carteira.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS carteira (user_id INTEGER PRIMARY KEY, nome TEXT, saldo REAL DEFAULT 0)")
cur.execute("CREATE TABLE IF NOT EXISTS pedidos (client_ref TEXT PRIMARY KEY, user_id INTEGER, valor REAL, status TEXT)")
conn.commit()

def pegar_saldo(uid, nome=""):
    cur.execute("SELECT saldo FROM carteira WHERE user_id=?", (uid,))
    r = cur.fetchone()
    if not r:
        cur.execute("INSERT INTO carteira VALUES (?,?,0)", (uid, nome)); conn.commit(); return 0.0
    return r[0]

def adicionar_saldo(uid, valor):
    novo = pegar_saldo(uid) + valor
    cur.execute("UPDATE carteira SET saldo=? WHERE user_id=?", (novo, uid)); conn.commit()
    return novo

# ===== GLADEPAY =====
def criar_pix_gladepay(user_id, valor):
    client_ref = f"user{user_id}-{uuid.uuid4().hex[:8]}"
    headers = {"X-API-Key": GLADE_API_KEY, "Content-Type": "application/json"}
    payload = {"amount": float(valor), "clientReference": client_ref, "callbackUrl": CALLBACK_URL}
    url = f"{BASE_URL}{ENDPOINT_CRIAR_PIX}"
    r = requests.post(url, json=payload, headers=headers, timeout=20)
    print("Gladepay:", r.status_code)
    dados = r.json()
    cur.execute("INSERT INTO pedidos VALUES (?,?,?,?)", (client_ref, user_id, float(valor), "PENDING")); conn.commit()
    return {
        "clientReference": client_ref,
        "qrCodeText": dados.get("qrCodeText"),
        "qrCodeUrl": dados.get("qrCodeUrl"),
        "fee": dados.get("fee"),
        "netAmount": dados.get("netAmount"),
        "id": dados.get("id")
    }

# ===== BOT TELEGRAM =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    saldo = pegar_saldo(user.id, user.first_name)
    teclado = [
        [InlineKeyboardButton("💰 Saldo", callback_data="saldo"),
         InlineKeyboardButton("⚡ Recarregar", callback_data="recarregar")],
        [InlineKeyboardButton("🛒 Comprar", callback_data="comprar")]
    ]
    await update.message.reply_text(
        f"👋 Olá {user.first_name}!\n\n🪪 ID: {user.id}\n💰 Saldo: R$ {saldo:.2f}\n\nEscolha uma opção:",
        reply_markup=InlineKeyboardMarkup(teclado)
    )

async def botoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    data = q.data

    if data == "saldo":
        saldo = pegar_saldo(user.id, user.first_name)
        await q.message.reply_text(f"💰 Seu saldo: R$ {saldo:.2f}\n🪪 ID: {user.id}")

    elif data == "recarregar":
        teclado = [
            [InlineKeyboardButton("R$ 10", callback_data="pix_10"), InlineKeyboardButton("R$ 20", callback_data="pix_20")],
            [InlineKeyboardButton("R$ 50", callback_data="pix_50"), InlineKeyboardButton("R$ 100", callback_data="pix_100")],
            [InlineKeyboardButton("⬅️ Voltar", callback_data="voltar")]
        ]
        await q.message.reply_text("⚡ Escolha o valor da recarga:", reply_markup=InlineKeyboardMarkup(teclado))

    elif data.startswith("pix_"):
        valor = float(data.replace("pix_", ""))
        await q.message.reply_text("⏳ Gerando seu PIX, aguarda...")
        try:
            pix = criar_pix_gladepay(user.id, valor)
            texto = f"✅ PIX GERADO\n\n🪪 ID: {user.id}\n💰 Valor: R$ {valor:.2f}\n🧾 Taxa: R$ {pix['fee']}\n💵 Você recebe: R$ {pix['netAmount']}\n⏰ Validade: 1 hora\n\n📋 Copia e cola:\n`{pix['qrCodeText']}`\n\nApós pagar, seu saldo cai sozinho aqui."
            if pix['qrCodeUrl']:
                await q.message.reply_photo(photo=pix['qrCodeUrl'], caption=texto, parse_mode="Markdown")
            else:
                await q.message.reply_text(texto, parse_mode="Markdown")
        except Exception as e:
            print("Erro PIX:", e)
            await q.message.reply_text("❌ Erro ao gerar PIX. Tenta de novo em 1 min.")

    elif data == "comprar":
        saldo = pegar_saldo(user.id, user.first_name)
        teclado = [[InlineKeyboardButton("📦 Produto R$ 10 - Comprar", callback_data="comprar_10")]]
        await q.message.reply_text(f"🛒 Loja\n\n💰 Seu saldo: R$ {saldo:.2f}", reply_markup=InlineKeyboardMarkup(teclado))

    elif data == "comprar_10":
        saldo = pegar_saldo(user.id)
        if saldo < 10:
            await q.message.reply_text("❌ Saldo insuficiente. Recarregue primeiro.")
        else:
            novo = adicionar_saldo(user.id, -10)
            await q.message.reply_text(f"✅ Compra feita!\n💰 Novo saldo: R$ {novo:.2f}")

    elif data == "voltar":
        await start(q, context)

# ===== WEBHOOK GLADEPAY - CAI O SALDO SOZINHO =====
app = Flask(__name__)

@app.route("/webhook/gladpay", methods=["POST"])
def webhook_gladpay():
    dados = request.json or {}
    print("Webhook Gladepay:", dados)
    status = str(dados.get("status", "")).upper()
    client_ref = dados.get("clientReference", "")
    if status in ["APPROVED", "PAID", "COMPLETED", "CONFIRMED", "SUCCESS"]:
        cur.execute("SELECT user_id, valor, status FROM pedidos WHERE client_ref=?", (client_ref,))
        p = cur.fetchone()
        if p and p[2] != "PAID":
            user_id, valor, _ = p
            cur.execute("UPDATE pedidos SET status='PAID' WHERE client_ref=?", (client_ref,)); conn.commit()
            novo = adicionar_saldo(user_id, valor)
            print(f"✅ Saldo adicionado: user {user_id} R$ {valor} novo saldo {novo}")
            # Aqui você pode mandar mensagem pro user via bot se quiser
    return jsonify({"ok": True})

@app.route("/", methods=["GET"])
def home():
    return "Bot online!"

def rodar_flask():
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ===== INICIA TUDO =====
def main():
    threading.Thread(target=rodar_flask, daemon=True).start()
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(botoes))
    print("Bot rodando...")
    application.run_polling()

if __name__ == "__main__":
    main()
