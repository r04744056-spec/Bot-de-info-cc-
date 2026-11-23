import os
import uuid
import sqlite3
import threading
import requests
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import re
from datetime import datetime

# ================= CONFIGURAÇÕES =================
load_dotenv()

TOKEN = os.getenv("API_TOKEN")
GLADE_API_KEY = os.getenv("GLADE_API")
BASE_URL = os.getenv("GLADE_URL", "https://api.gladepay.com/v1") # Ajuste se necessário
CALLBACK_URL = os.getenv("CALLBACK_URL", "https://seusite.com/callback") # Exemplo genérico
ADMIN_ID = 8742219720 # <-- TROQUE PELO SEU ID NUMÉRICO DO TELEGRAM

app = Flask(__name__)

# ================= BANCO DE DADOS =================
conn = sqlite3.connect("carteira.db", check_same_thread=False)
cur = conn.cursor()

# Tabelas Originais (Saldo/PIX)
cur.execute("CREATE TABLE IF NOT EXISTS carteira (user_id INTEGER PRIMARY KEY, nome TEXT, saldo REAL DEFAULT 0)")
cur.execute("CREATE TABLE IF NOT EXISTS pedidos (client_ref TEXT PRIMARY KEY, user_id INTEGER, valor REAL, status TEXT)")

# NOVA TABELA: LOJA DE CCs
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

def chk_data(cartao_dict):
    """Verifica se o cartão não venceu."""
    try:
        mes = int(cartao_dict['mes_expiracao'])
        ano = int(cartao_dict['ano_expiracao'])
        hoje = datetime.now()
        data_exp = datetime(int(ano), mes, 1)
        if data_exp < hoje:
            return False, "Vencido"
    except:
        pass
    return True, "Data OK"

def buscar_produtos(tipo, valor):
    """Busca no banco de dados (BIN, Banco ou Nível)"""
    query = ""
    params = []
    
    if tipo == "bin":
        query = "SELECT * FROM produtos WHERE bin_code LIKE ? AND estoque > 0"
        params = (f"%{valor}%",)
    elif tipo == "banco":
        query = "SELECT * FROM produtos WHERE LOWER(banco) LIKE ? AND estoque > 0"
        params = (f"%{valor.lower()}%",)
    elif tipo == "nivel":
        query = "SELECT * FROM produtos WHERE LOWER(nivel) LIKE ? AND estoque > 0"
        params = (f"%{valor.lower()}%",)
    else:
        return []

    cur.execute(query, params)
    return cur.fetchall()

def formatar_card(prod):
    """Formata um produto do DB para string legível."""
    # prod é uma tupla: (id, bin, banco, nivel, bandeira, mes, ano, cvv, titular, live, estoque, preco)
    return (
        f"💳 **{prod[4]}**\n"
        f"🏦 Banco: {prod[2]}\n"
        f"📅 {prod[5]}/{prod[5]}\n"
        f"🔞 Nível: {prod[3]}\n"
        f"🛡️ Status: {'LIVE' if prod[8] else 'MORTO'}\n"
        f"💰 Preço: R$ {prod[11]:.2f}"
    )

# ================= TELEGRAM BOT =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    cur.execute("SELECT saldo FROM carteira WHERE user_id=?", (user.id,))
    if not cur.fetchone():
        cur.execute("INSERT INTO carteira VALUES (?, ?, 0)", (user.id, user.first_name))
        conn.commit()
        
    await update.message.reply_text(
        f"Olá {user.first_name}! 👋\n\n"
        "Selecione uma opção abaixo:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Minha Carteira", callback_data="wallet"),
             InlineKeyboardButton("📤 Pix", callback_data="pix")],
            [InlineKeyboardButton("🛒 Loja de CCs", callback_data="store")],
            [InlineKeyboardButton("📜 Histórico", callback_data="history")]
        ])
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    
    if data == "wallet":
        saldo = pegar_saldo(user_id)
        await query.edit_message_text(f"💰 Sua Carteira:\n\nSaldo Atual: R$ {saldo:.2f}")

    elif data == "pix":
        await query.edit_message_text(
            "📤 Criar PIX\n\nDigite o valor que deseja receber (ex: 10.00)"
        )
        # Aguarda resposta
        context.user_data['waiting_for_pix'] = True

    elif data == "store":
        keyboard = [
            [InlineKeyboardButton("Buscar por BIN 🔍", callback_data="search_bin")],
            [InlineKeyboardButton("Buscar por Banco 🏦", callback_data="search_bank")],
            [InlineKeyboardButton("Buscar por Nível 🎓", callback_data="search_level")]
        ]
        await query.edit_message_text("🛒 Selecione como quer buscar:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "search_bin":
        await query.edit_message_text("Envie o BIN (ex: 4111) para buscar:")
        context.user_data['search_type'] = 'bin'
        
    elif data == "search_bank":
        await query.edit_message_text("Nome do banco (ex: Inter, Nubank, BB):")
        context.user_data['search_type'] = 'banco'
        
    elif data == "search_level":
        await query.edit_message_text("Nível (ex: Gold, Black, High):")
        context.user_data['search_type'] = 'nivel'

    elif data.startswith("buy_"):
        product_id = int(data.split("_")[1])
        cur.execute("SELECT * FROM produtos WHERE id=?", (product_id,))
        prod = cur.fetchone()
        
        # CHK INTEGRADO
        is_valid_date, msg_date = chk_data({
            'mes_expiracao': prod[5], 
            'ano_expiracao': prod[6]
        })
        
        full_number = f"{prod[1]} {prod[5]} {prod[7]} {prod[8]}" # Bin + Mes + CVV + Titular (ajuste conforme seu formato real)
        # Nota
                # Monta o número completo para o CHK (ajuste as variáveis conforme seu DB)
        # Assumindo: prod[1]=Bin, prod[5]=Mes, prod[7]=CVV, prod[8]=Titular
        # Mas geralmente precisamos do número completo. Vou usar uma string formatada baseada nos campos.
        # Se você tiver o número completo salvo em outro campo, substitua 'full_number' abaixo.
        full_number = f"{prod[1]}{prod[5]}{prod[7]}" # Exemplo genérico
        
        # 1. CHK Luhn
        if not luhn_check(full_number):
            await query.edit_message_text(f"❌ **CHK Falhou:** Número inválido (Luhn).")
            return

        # 2. CHK Data
        if not is_valid_date:
            await query.edit_message_text(f"❌ **CHK Falhou:** Cartão vencido ({msg_date}).")
            return

        # Se passou no CHK:
        preco = prod[11]
        saldo = pegar_saldo(user_id)
        
        if saldo < preco:
            await query.edit_message_text(f"💸 Saldo insuficiente!\n\nVocê tem: R$ {saldo:.2f}\nCusta: R$ {preco:.2f}")
            return

        # Compra com sucesso
        adicionar_saldo(user_id, -preco)
        
        # Diminui estoque
        cur.execute("UPDATE produtos SET estoque = estoque - 1 WHERE id=?", (product_id,))
        conn.commit()

        # Formata a resposta final
        resumo = (
            f"✅ **Compra Realizada!**\n\n"
            f"🔢 Número: `{full_number}`\n"
            f"🏦 Banco: {prod[2]}\n"
            f"📅 Validade: {prod[5]}/{prod[6]}\n"
            f"🔐 CVV: {prod[7]}\n"
            f"👤 Titular: {prod[8]}\n"
            f"🛡️ Status: {prod[4]}"
        )
        
        await query.edit_message_text(resumo, parse_mode="Markdown", disable_web_page_preview=True)

    elif data == "history":
        cur.execute("SELECT * FROM pedidos WHERE user_id=? ORDER BY id DESC LIMIT 10", (user_id,))
        rows = cur.fetchall()
        if not rows:
            msg = "📜 Sem histórico de transações."
        else:
            msg = "📜 **Últimas Transações:**\n\n"
            for r in rows:
                msg += f"Ref: `{r[0]}`\nValor: R$ {r[2]:.2f}\nStatus: {r[3]}\n\n"
        
        await query.edit_message_text(msg, parse_mode="Markdown")

async def handle_pix_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    if context.user_data.get('waiting_for_pix'):
        try:
            valor = float(text.replace(',', '.'))
            if valor <= 0:
                await update.message.reply_text("⚠️ Valor deve ser maior que zero.")
                return

            # Chamar API Gladepay
            headers = {"Authorization": f"Bearer {GLADE_API_KEY}"}
            payload = {
                "value": int(valor * 100), # Gladepay costuma pedir em centavos
                "description": f"PIX Recarga User {user_id}",
                "callback_url": CALLBACK_URL
            }
            
            # Ajuste a URL exata da sua documentação Gladepay
            url = f"{BASE_URL}/pix/create" 
            
            resp = requests.post(url, json=payload, headers=headers)
            data = resp.json()
            
            if resp.status_code == 200 or 'id' in data:
                qr_code = data.get('qr_code', '') # Campo pode variar
                pix_copia_colava = data.get('payload', '')
                
                msg = f"✅ PIX Criado!\n\nValor: R$ {valor:.2f}\n\nCopie o código abaixo:\n\n`{pix_copia_colava}`"
                await update.message.reply_text(msg, parse_mode="Markdown")
            else:
                await update.message.reply_text(f"❌ Erro ao criar PIX: {data.get('message', str(data))}")
                
        except ValueError:
            await update.message.reply_text("⚠️ Digite um valor numérico válido (ex: 10.50)")
        finally:
            context.user_data['waiting_for_pix'] = False

async def handle_search_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    search_type = context.user_data.get('search_type')
    query_term = update.message.text.strip()
    
    if not search_type:
        return

    results = buscar_produtos(search_type, query_term)
    
    if not results:
        await update.message.reply_text("❌ Nenhum produto encontrado.")
        context.user_data['search_type'] = None
        return

    keyboard = []
    for prod in results:
        # botao: buy_{id}
        btn_text = f"{prod[4]} - R$ {prod[11]:.2f}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"buy_{prod[0]}")])
    
    await update.message.reply_text(
        f"🔍 Resultados para '{query_term}':",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    context.user_data['search_type'] = None

# ================= SETUP DO BOT =================

def main():
    if not TOKEN:
        print("Erro: Token do Telegram não encontrado. Verifique o .env")
        return

    application = Application.builder().token(TOKEN).build()

    # Handlers Principais
    application.add_handler(CommandHandler("start", start))
    
    # Handler de Callbacks (Botões)
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Handler para resposta do PIX
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_pix_response))
    
    # Handler para busca na loja
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_response))

    print("Bot iniciado...")
    application.run_polling()

if __name__ == '__main__':
    main()

# ADICIONE ESTE HANDLER AO SEU BOT NO FINAL DO CÓDIGO ANTERIOR

async def add_product_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # Verifica se é admin (troque ID se necessário)
    if user.id != 8742219720: 
        await update.message.reply_text("🔒 Apenas administradores podem adicionar produtos.")
        return

    # Espera a resposta com os dados
    # Formato esperado: BIN|BANCO|NIVEL|BANDEIRA|MES|ANO|CVV|TITULAR|PRECO
    # Exemplo: 4111111111111111|Itau|Gold|Visa|12|2028|123|Joao Silva|15.00
    
    if not context.user_data.get('waiting_for_add'):
        await update.message.reply_text(
            "📝 Modo Admin: Envie os dados no formato:\n\n"
            "`BIN|Banco|Nivel|Bandeira|Mes|Ano|CVV|Titular|Preco`\n\n"
            "Exemplo: `4111111111111111|Itau|Gold|Visa|12|2028|123|Joao|15.00`"
        )
        context.user_data['waiting_for_add'] = True
        return

    data = update.message.text.strip().split('|')
    
    if len(data) < 9:
        await update.message.reply_text("⚠️ Formato incorreto. Use: `BIN|Banco|Nivel|Bandeira|Mes|Ano|CVV|Titular|Preco`")
        context.user_data['waiting_for_add'] = False
        return

    try:
        bin_code = data[0]
        banco = data[1]
        nivel = data[2]
        bandeira = data[3]
        mes = data[4]
        ano = data[5]
        cvv = data[6]
        titular = data[7]
        preco = float(data[8])

        cur.execute("""
            INSERT INTO produtos (bin_code, banco, nivel, bandeira, mes_expiracao, ano_expiracao, cvv, titular, preco)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (bin_code, banco, nivel, bandeira, mes, ano, cvv, titular, preco))
        conn.commit()

        await update.message.reply_text(f"✅ Produto adicionado!\n\n💳 {bin_code}\n🏦 {banco} ({nivel})")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Erro ao adicionar: {str(e)}")
    
    context.user_data['waiting_for_add'] = False

# REGISTRE O COMANDO NO MAIN():
# application.add_handler(CommandHandler("addcc", lambda u, c: setattr(c.user_data, 'waiting_for_add', True) or add_product_handler(u, c)))
# Ou melhor, use um CommandHandler separado:
# application.add_handler(CommandHandler("addcc", start_add_mode))
   # ============ BANCO ============
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS carteiras (user_id INTEGER PRIMARY KEY, saldo REAL DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS recargas (id TEXT PRIMARY KEY, user_id INTEGER, valor REAL, status TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS mp_pagamentos (recarga_id TEXT PRIMARY KEY, payment_id TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS gifts (codigo TEXT PRIMARY KEY, valor REAL, usado INTEGER DEFAULT 0, usado_por INTEGER)""")
    c.execute("""CREATE TABLE IF NOT EXISTS pedidos (id TEXT PRIMARY KEY, user_id INTEGER, plano TEXT, preco REAL, status TEXT)""")
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

# ============ MERCADO PAGO ============
def criar_pix_mp(valor, user_id, recarga_id):
    url = "https://web-production-73dbd.up.railway.app/docs"
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
