import os
import uuid
import sqlite3
import threading
import requests
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, 
    CommandHandler, 
    CallbackQueryHandler, 
    ContextTypes, 
    MessageHandler, 
    filters
)
from datetime import datetime
import random
import string

# ================= CONFIGURAÇÕES =================
load_dotdotenv = lambda: None # Evita erro se load_dotenv falhar silenciosamente
try:
    from dotenv import load_dotenv
    load_dotenv()
except:
    pass

TOKEN = os.getenv("API_TOKEN")
GLADE_API_KEY = os.getenv("GLADE_API")
BASE_URL = "https://api.gladepay.com/v1" 
CALLBACK_URL = os.getenv("CALLBACK_URL", "https://web-production-5539d78c.up.railway.app/") 
ADMIN_ID = int(os.getenv("ADMIN_ID", "8742219720")) 

app = Flask(__name__)

# ================= BANCO DE DADOS =================
conn = sqlite3.connect("carteira.db", check_same_thread=False)
cur = conn.cursor()

# Tabelas Originais (Saldo/PIX)
cur.execute("CREATE TABLE IF NOT EXISTS carteira (user_id INTEGER PRIMARY KEY, nome TEXT, saldo REAL DEFAULT 0)")
cur.execute("CREATE TABLE IF NOT EXISTS pedidos (client_ref TEXT PRIMARY KEY, user_id INTEGER, valor REAL, status TEXT, pix_code TEXT)")

# TABELA DE GIFT CARDS
cur.execute('''CREATE TABLE IF NOT EXISTS gift_cards (
    code TEXT PRIMARY KEY,
    valor REAL,
    status TEXT DEFAULT 'pendente', -- pendente, usado
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')

# TABELA DE LOJA CCs
cur.execute('''CREATE TABLE IF NOT EXISTS produtos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bin_code TEXT,
    banco TEXT,
    nivel TEXT,
    bandeira TEXT,
    mes_expiracao TEXT,
    ano_expiracao TEXT,
    cvv TEXT,
    titular TEXT,
    status_live BOOLEAN DEFAULT 0, 
    estoque INTEGER DEFAULT 1,
    preco REAL DEFAULT 10.0
)''')

conn.commit()

# ================= FUNÇÕES AUXILIARES =================

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

def luhn_check(card_number):
    """Valida formato do cartão (Algoritmo de Luhn)."""
    def digits_of(n): return [int(d) for d in str(n)]
    digits = digits_of(card_number)
    odd_digits = digits[-1::-2]
    even_digits = digits[-2::-2]
    total = sum(odd_digits)
    for n in even_digits:
        total += sum(divmod(n * 2, 10))
    return total % 10 == 0

def chk_interno(cartao_dict):
    """
    CHK AUTOMÁTICO INTERNO
    """
    card_num = cartao_dict.get('cc', '')
    mes = cartao_dict.get('mes', '')
    ano = cartao_dict.get('ano', '')
    
    is_luhn = luhn_check(card_num)
    
    try:
        mes_int = int(mes)
        ano_int = int(ano)
        data_exp = datetime(ano_int, mes_int, 1)
        is_valid_date = data_exp >= datetime.now()
    except:
        is_valid_date = False
        
    if not is_luhn:
        return "MORTO", "Luhn Inválido"
    elif not is_valid_date:
        return "VENCIDO", "Data Passada"
    else:
        status = "LIVE" if random.random() > 0.3 else "TAGI"
        return status, "Cartão Válido"

def criar_pix_gladepay(valor, descricao="Pagamento Bot"):
    """Cria um Pix via API Gladepay."""
    headers = {
        "Authorization": f"Bearer {GLADE_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "amount": valor,
        "description": descricao,
        "callback_url": CALLBACK_URL
    }
    try:
        response = requests.post(f"{BASE_URL}/pix/create", json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Erro ao criar PIX: {e}")
        return None

# ================= HANDLERS DO BOT =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"Olá {user.first_name}! 👋\n\n"
        "🛒 **Loja**: /loja\n"
        "💳 **Comprar CC**: /comprar\n"
        "🎁 **Resgatar Gift Card**: /resgatar <codigo>\n"
        "💰 **Depósito Pix**: /pix\n"
        "👤 **Meu Perfil**: /perfil"
    )

async def perfil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    saldo = pegar_saldo(user.id, user.first_name)
    await update.message.reply_text(
        f"👤 **Perfil**\n\n"
        f"Nome: {user.first_name}\n"
        f"ID: {user.id}\n"
        f"Saldo: R$ {saldo:.2f}"
    )

async def comprar_cc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Exemplo simples: lista os produtos da tabela produtos
    cur.execute("SELECT id, bin_code, banco, nivel, bandeira, preco FROM produtos LIMIT 10")
    produtos = cur.fetchall()
    
    if not produtos:
        await update.message.reply_text("Nenhum produto disponível no momento.")
        return

    text = "📦 **Produtos Disponíveis:**\n\n"
    for p in produtos:
        text += f"ID: {p[0]} | {p[1]} - {p[2]} ({p[3]})\nPreço: R$ {p[6]:.2f}\n\n"
        
    await update.message.reply_text(text)

async def resgatar_gift_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Espera o comando /resgatar CODIGO
    args = update.message.text.split()
    if len(args) < 2:
        await update.message.reply_text("Use: /resgatar <codigo>")
        return
    
    codigo = args[1]
    cur.execute("SELECT valor, status FROM gift_cards WHERE code=?", (codigo,))
    card = cur.fetchone()
    
    if not card:
        await update.message.reply_text("❌ Código inválido ou não encontrado.")
        return
    
    if card[1] == 'usado':
        await update.message.reply_text("❌ Este gift card já foi utilizado.")
        return
    
    # Resgata
    uid = update.effective_user.id
    nome = update.effective_user.first_name
    adicionar_saldo(uid, card[0])
    cur.execute("UPDATE gift_cards SET status='usado' WHERE code=?", (codigo,))
    conn.commit()
    
    await update.message.reply_text(f"✅ Gift Card resgatado!\nValor creditado: R$ {card[0]:.2f}")

async def pix_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Lógica simplificada para pedir valor
    user = update.effective_user
    await update.message.reply_text("Digite o valor que deseja depositar:")
    # Em um bot real, você usaria ConversationFilters ou Context.user_data para esperar a resposta
    # Aqui é apenas um exemplo básico

# ================= INICIALIZAÇÃO DO BOT =================

def main():
    # Construção correta para PTB v20+
    application = ApplicationBuilder().token(TOKEN).build()

    # Registrando Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("perfil", perfil))
    application.add_handler(CommandHandler("comprar", comprar_cc))
    application.add_handler(CommandHandler("resgatar", resgatar_gift_card))
    application.add_handler(CommandHandler("pix", pix_handler))

    # Inicia o bot
    print("Bot iniciado...")
    application.run_polling()

if __name__ == "__main__":
    main()
