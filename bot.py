import os
import uuid
import sqlite3
import threading
import requests
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from datetime import datetime
import random
import string

# ================= CONFIGURAÇÕES =================
load_dotenv()

TOKEN = os.getenv("API_TOKEN")
GLADE_API_KEY = os.getenv("GLADE_API")
BASE_URL = "https://api.gladepay.com/v1" # URL Base da Gladepay
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

# TABELA DE LOJA CCs (Mantida conforme seu exemplo anterior)
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
    CHK AUTOMÁTICO INTERNO (Sem Gateway Gladepay)
    Verifica: Luhn, Data, e simula Live/Tagi baseado em padrões comuns.
    """
    card_num = cartao_dict.get('cc', '')
    mes = cartao_dict.get('mes', '')
    ano = cartao_dict.get('ano', '')
    
    # 1. Validação Luhn
    is_luhn = luhn_check(card_num)
    
    # 2. Validação de Data
    try:
        mes_int = int(mes)
        ano_int = int(ano)
        data_exp = datetime(ano_int, mes_int, 1)
        is_valid_date = data_exp >= datetime.now()
    except:
        is_valid_date = False
        
    # 3. Simulação de Status (Lógica interna simples)
    # Se Luhn falhar -> MORTO
    # Se Data passar -> VENCIDO
    # Se Luhn e Data OK -> Aleatório entre LIVE e TAGI (simulando verificação real)
    
    if not is_luhn:
        return "MORTO", "Luhn Inválido"
    elif not is_valid_date:
        return "VENCIDO", "Data Passada"
    else:
        # Simula 70% de chance de ser Live e 30% Tagi para cards válidos
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
        "type": "pix", # ou 'link' dependendo do plano da Gladepay
        "callback_url": CALLBACK_URL,
        "expires_in": 1800 # 30 minutos
    }
    
    try:
        response = requests.post(f"{BASE_URL}/charges", json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        # Ajuste conforme a estrutura exata da resposta da Gladepay
        # Geralmente retorna 'data' com 'id' e 'qr_code' ou 'link'
        if 'data' in data:
            return data['data']
        return data
    except Exception as e:
        print(f"Erro ao gerar Pix: {e}")
        return None

def resgatar_gift_card(code, user_id):
    """Resgata um código de gift card."""
    cur.execute("SELECT valor, status FROM gift_cards WHERE code=?", (code,))
    row = cur.fetchone()
    
    if not row:
        return False, "Código não encontrado."
    
    valor, status = row
    
    if status == 'usado':
        return False, "Este código já foi resgatado."
    
    # Marca como usado
    cur.execute("UPDATE gift_cards SET status='usado' WHERE code=?", (code,))
    conn.commit()
    
    # Adiciona saldo
    adicionar_saldo(user_id, valor)
    return True, f"Código resgatado! Saldo adicionado: R$ {valor:.2f}"

def criar_gift_card(valor, admin_id=None):
    """Cria um novo código de gift card."""
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))
    cur.execute("INSERT INTO gift_cards (code, valor) VALUES (?, ?)", (code, valor))
    conn.commit()
    return code

# ================= TELEGRAM BOT =================

async def start(update: Update, context: ContextTypes.DEFAULT_USER_DATA):
    user = update.effective_user
    await update.message.reply_text(
        f"Olá {user.first_name}! 👋\n\n"
        "🛒 **Loja**: /loja\n"
        "💳 **Comprar CC**: /comprar\n"
        "🎁 **Resgatar Gift Card**: /resgatar <codigo>\n"
        "💰 **Depósito Pix**: /pix\n"
        "👤 **Meu Perfil**: /perfil"):

async def perfil(update: Update, context: ContextTypes.DEFAULT_USER_DATA):
    user = update.effective_user
    saldo = pegar_saldo(user.id, user.first_name)
    await update.message.reply_text(f"💳 **Seu Perfil**\n\n👤 Nome: {user.first_name}\n💰 Saldo: R$ {saldo:.2f}")

async def comprar_cc(update: Update, context: ContextTypes.DEFAULT_USER_DATA):
    # Exemplo de compra de um CC específico ou lista
    # Aqui você pode integrar com a tabela produtos
    keyboard = [
        [InlineKeyboardButton("Ver Produtos", callback_data='ver_produtos')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Escolha uma opção:", reply_markup=reply_markup)

async def chk_cartao(update: Update, context: ContextTypes.DEFAULT_USER_DATA):
    """
    Comando /chk cc:mes:ano:cvv
    Usa o CHK interno (sem Gladepay)
    """
    if len(context.args) < 1:
        await update.message.reply_text("Uso: /chk cc:mes:ano:cvv")
        return
        
    raw_card = context.args[0]
    parts = raw_card.split(':')
    
    if len(parts) != 4:
        await update.message.reply_text("Formato inválido. Use: cc:mes:ano:cvv")
        return
        
    cc, mes, ano, cvv = parts
    
    card_info = {'cc': cc, 'mes': mes, 'ano': ano}
    status, msg = chk_interno(card_info)
    
    emoji = "✅" if status == "LIVE" else "❌"
    if status == "TAGI":
        emoji = "⚠️"
        
    await update.message.reply_text(f"{emoji} **CHK Result**\n\n"
                                    f"💳 Número: {cc}\n"
                                    f"🏦 Status: {status}\n"
                                    f"ℹ️ Detalhe: {msg}")

async def resgatar_command(update: Update, context: ContextTypes.DEFAULT_USER_DATA):
    if len(context.args) < 1:
        await update.message.reply_text("Uso: /resgatar <codigo>")
        return
        
    code = context.args[0].upper()
    user_id = update.effective_user.id
    
    success, msg = resgatar_gift_card(code, user_id)
    
    if success:
        await update.message.reply_text(f"🎉 Sucesso!\n\n{msg}")
    else:
        await update.message.reply_text(f"❌ Erro:\n\n{msg}")

async def pix_command(update: Update, context: ContextTypes.DEFAULT_USER_DATA):
    user = update.effective_user
    # Valor padrão ou pedido pelo usuário
    valor = float(context.args[0]) if context.args and context.args[0].replace('.','',1).isdigit() else 10.0
    
    # Chama Gladepay
    pix_data = criar_pix_gladepay(valor, f"Depósito User {user.id}")
    
    if pix_data:
        # Supondo que Gladepay retorne um QR Code ou Link
        qr_code_url = pix_data.get('qr_code', pix_data.get('link', ''))
        
        text = f"💰 **Gerar Pix de R$ {valor:.2f}**\n\n"
        if qr_code_url:
            text += f"Acesse aqui: {qr_code_url}\n\nOu use a chave Pix abaixo:"
            # Se for chave estática, mostre ela
            await update.message.reply_text(text)
        else:
            await update.message.reply_text("Erro ao gerar Pix.")
    else:
        await update.message.reply_text("Erro ao conectar com Gladepay.")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_USER_DATA):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'ver_produtos':
        await query.edit_message_text("Listagem de produtos...")
    elif query.data.startswith('buy_'):
        product_id = query.data.split('_')[1]
        # Lógica de compra...

# ================= FLASK WEBHOOK (Para Callback da Gladepay) =================

@app.route('/callback', methods=['POST'])
def gladepay_callback():
    """Recebe o webhook da Gladepay quando o Pix é pago."""
    data = request.json
    # Verifica se o pagamento foi aprovado
    if data.get('status') == 'paid' or data.get('payment_status') == 'completed':
        client_ref = data.get('client_ref') # ID único que você passou na criação
        amount = data.get('amount')
        
        # Encontra o pedido no banco
        cur.execute("SELECT user_id FROM pedidos WHERE client_ref=?", (client_ref,))
        row = cur.fetchone()
        
        if row:
            user_id = row[0]
            adicionar_saldo(user_id, amount)
            
            # Notifica o usuário (opcional, requer armazenar chat_id no pedido)
            # send_notification(user_id, f"Pagamento confirmado! Saldo atualizado.")
            
    return jsonify({"status": "ok"})

# ================= INICIALIZAÇÃO =================

def main():
    application = Application.builder().token(TOKEN).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("perfil", perfil))
    application.add_handler(CommandHandler("pix", pix_command))
    application.add_handler(CommandHandler("chk", chk_cartao))
    application.add_handler(CommandHandler("resgatar", resgatar_command))
    application.add_handler(CommandHandler("comprar", comprar_cc))
    application.add_handler(CallbackQueryHandler(callback_handler))
    
    print("Bot iniciado...")
    application.run_polling()

if __name__ == '__main__':
    main()
