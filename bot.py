import os
import sqlite3
import uuid
import requests
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

load_dotenv()
TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
MP_TOKEN = os.getenv("MP_ACCESS_TOKEN")

DB_PATH = "/data/vendas.db" if os.path.exists("/data") else "vendas.db"
print(f"Usando banco em: {DB_PATH}")

PLANOS = {
    "netflix_1mes": {"nome": "Netflix - 1 Mes", "preco": 25.90},
    "netflix_3meses": {"nome": "Netflix - 3 Meses", "preco": 69.90},
}

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS carteiras (user_id INTEGER PRIMARY KEY, saldo REAL DEFAULT 0)")
    c.execute("CREATE TABLE IF NOT EXISTS recargas (id TEXT PRIMARY KEY, user_id INTEGER, valor REAL, status TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS mp_pagamentos (recarga_id TEXT PRIMARY KEY, payment_id TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS gifts (codigo TEXT PRIMARY KEY, valor REAL, usado INTEGER DEFAULT 0, usado_por INTEGER)")
    c.execute("CREATE TABLE IF NOT EXISTS pedidos (id TEXT PRIMARY KEY, user_id INTEGER, plano TEXT, preco REAL, status TEXT)")
    conn.commit()
    conn.close()

init_db()

def get_saldo(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO carteiras VALUES (?, 0)", (user_id,))
    c.execute("SELECT saldo FROM carteiras WHERE user_id=?", (user_id,))
    saldo = c.fetchone()[0]
    conn.commit()
    conn.close()
    return saldo

def add_saldo(user_id, valor):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO carteiras VALUES (?, 0)", (user_id,))
    c.execute("UPDATE carteiras SET saldo = saldo + ? WHERE user_id=?", (valor, user_id))
    conn.commit()
    conn.close()

def criar_pix_mp(valor, user_id, recarga_id):
    url = "https://api.mercadopago.com/v1/payments"
    headers = {"Authorization": f"Bearer {MP_TOKEN}", "Content-Type": "application/json", "X-Idempotency-Key": recarga_id}
    payload = {
        "transaction_amount": float(valor),
        "description": f"Recarga carteira - {recarga_id}",
        "payment_method_id": "pix",
        "payer": {"email": f"user{user_id}@loja.com", "first_name": "Cliente", "last_name": "Loja"},
        "external_reference": recarga_id
    }
    r = requests.post(url, json=payload, headers=headers)
    return r.json()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    saldo = get_saldo(user.id)
    keyboard = [
        [InlineKeyboardButton("Comprar Netflix", callback_data="loja")],
        [InlineKeyboardButton("Minha Carteira", callback_data="ver_carteira")],
        [InlineKeyboardButton("Recarregar /pix", callback_data="como_pix")],
        [InlineKeyboardButton("Resgatar Gift", callback_data="como_resgatar")],
    ]
    await update.message.reply_text(f"Bem-vindo!\n\nSeu saldo: R$ {saldo:.2f}", reply_markup=InlineKeyboardMarkup(keyboard))

async def meu_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Seu ID e: {update.effective_user.id}")

async def carteira_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    saldo = get_saldo(update.effective_user.id)
    await update.message.reply_text(f"Sua Carteira\n\nSaldo atual: R$ {saldo:.2f}\n\nUse /pix 25 para recarregar R$ 25")

async def pix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Uso: /pix 25")
        return
    try:
        valor = float(context.args[0].replace(",", "."))
    except:
        await update.message.reply_text("Valor invalido. Ex: /pix 25")
        return
    if valor < 5:
        await update.message.reply_text("Minimo R$ 5,00")
        return
    recarga_id = str(uuid.uuid4())[:8].upper()
    dados = criar_pix_mp(valor, user.id, recarga_id)
    if "id" not in dados:
        await update.message.reply_text(f"Erro ao gerar PIX.\n{dados}")
        return
    payment_id = str(dados["id"])
    copia_cola = dados["point_of_interaction"]["transaction_data"]["qr_code"]
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO recargas VALUES (?, ?, ?, ?)", (recarga_id, user.id, valor, "aguardando"))
    c.execute("INSERT INTO mp_pagamentos VALUES (?, ?)", (recarga_id, payment_id))
    conn.commit()
    conn.close()
    keyboard = [[InlineKeyboardButton("Ja paguei, verificar", callback_data=f"verificar_{recarga_id}")]]
    await update.message.reply_text(f"Recarga criada\n\nID: {recarga_id}\nValor: R$ {valor:.2f}\n\nPIX Copia e Cola:\n{copia_cola}", reply_markup=InlineKeyboardMarkup(keyboard))

async def verificar_pagamento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Verificando...")
    recarga_id = query.data.replace("verificar_", "")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT payment_id FROM mp_pagamentos WHERE recarga_id=?", (recarga_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        await query.message.reply_text("Recarga nao encontrada.")
        return
    r = requests.get(f"https://api.mercadopago.com/v1/payments/{row[0]}", headers={"Authorization": f"Bearer {MP_TOKEN}"})
    status = r.json().get("status")
    if status == "approved":
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT user_id, valor, status FROM recargas WHERE id=?", (recarga_id,))
        rec = c.fetchone()
        if rec and rec[2] == "aguardando":
            c.execute("UPDATE recargas SET status='aprovado' WHERE id=?", (recarga_id,))
            conn.commit()
            conn.close()
            add_saldo(rec[0], rec[1])
            await query.message.reply_text(f"Pagamento confirmado! R$ {rec[1]:.2f} adicionados!\nSaldo atual: R$ {get_saldo(rec[0]):.2f}")
        else:
            conn.close()
            await query.message.reply_text("Essa recarga ja foi aprovada.")
    elif status == "pending":
        await query.message.reply_text("Ainda nao caiu. Paga e tenta de novo em 1 min.")
    else:
        await query.message.reply_text(f"Status: {status}")

async def gerar_gift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("So o admin pode gerar gift.")
        return
    if not context.args:
        await update.message.reply_text("Uso: /gerargift 25")
        return
    valor = float(context.args[0].replace(",", "."))
    codigo = "GIFT-" + str(uuid.uuid4())[:6].upper()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO gifts VALUES (?, ?, 0, NULL)", (codigo, valor))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"Gift criado!\nCodigo: {codigo}\nValor: R$ {valor:.2f}")

async def resgatar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Uso: /resgatar GIFT-XXXXXX")
        return
    codigo = context.args[0].upper()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT valor, usado FROM gifts WHERE codigo=?", (codigo,))
    row = c.fetchone()
    if not row:
        await update.message.reply_text("Codigo invalido.")
        conn.close()
        return
    valor, usado = row
    if usado:
        await update.message.reply_text("Esse gift ja foi usado.")
        conn.close()
        return
    c.execute("UPDATE gifts SET usado=1, usado_por=? WHERE codigo=?", (user.id, codigo))
    conn.commit()
    conn.close()
    add_saldo(user.id, valor)
    await update.message.reply_text(f"Gift resgatado! Voce ganhou R$ {valor:.2f}!\nSaldo atual: R$ {get_saldo(user.id):.2f}")

async def botoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user
    if data == "ver_carteira":
        await query.message.reply_text(f"Seu saldo: R$ {get_saldo(user.id):.2f}\nUse /pix 25 para recarregar.")
        return
    if data == "como_pix":
        await query.message.reply_text("Para recarregar digite: /pix 25")
        return
    if data == "como_resgatar":
        await query.message.reply_text("Para resgatar digite: /resgatar GIFT-XXXXXX")
        return
    if data == "loja":
        keyboard = []
        for key, plano in PLANOS.items():
            keyboard.append([InlineKeyboardButton(f"{plano['nome']} - R$ {plano['preco']:.2f}", callback_data=f"comprar_{key}")])
        await query.message.reply_text("Loja Netflix\n\nEscolha um plano:", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    if data.startswith("comprar_"):
        plano_key = data.replace("comprar_", "")
        plano = PLANOS.get(plano_key)
        saldo = get_saldo(user.id)
        if saldo < plano["preco"]:
            await query.message.reply_text(f"Saldo insuficiente! Voce tem R$ {saldo:.2f}, precisa de R$ {plano['preco']:.2f}")
            return
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE carteiras SET saldo = saldo - ? WHERE user_id=?", (plano["preco"], user.id))
        pedido_id = str(uuid.uuid4())[:8].upper()
        c.execute("INSERT INTO pedidos VALUES (?, ?, ?, ?, ?)", (pedido_id, user.id, plano["nome"], plano["preco"], "pago"))
        conn.commit()
        conn.close()
        await query.message.reply_text(f"Compra confirmada!\nPedido: {pedido_id}\nPlano: {plano['nome']}\nSaldo restante: R$ {get_saldo(user.id):.2f}")
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"Nova venda!\nPedido: {pedido_id}\nCliente: {user.first_name} ID: {user.id}\nPlano: {plano['nome']}")
        return

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", meu_id))
    app.add_handler(CommandHandler("carteira", carteira_cmd))
    app.add_handler(CommandHandler("pix", pix))
    app.add_handler(CommandHandler("gerargift", gerar_gift))
    app.add_handler(CommandHandler("resgatar", resgatar))
    app.add_handler(CallbackQueryHandler(verificar_pagamento, pattern="^verificar_"))
    app.add_handler(CallbackQueryHandler(botoes))
    print("Bot rodando...")
    app.run_polling()

if __name__ == "__main__":
    main()
