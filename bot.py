# bot.py — Bot Telegram Completo (GladePay + CHK)
import logging
import requests
import json
import uuid
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    ContextTypes, CallbackQueryHandler, filters, ConversationHandler
)

# ======================== CONFIGURAÇÃO ========================
GLADE_API_KEY = "SUA_CHAVE_AQUI"  # ← Substitua pela sua API Key do @gladepaybot
BASE_URL = "https://web-production-73dbd.up.railway.app/api/v1"
CHK_URL = "http://143.20.50.71/cielo/"

# Estado dos usuários para conversas
STATE_DEPOSITO = 1
STATE_ADD_BINLEVEL = 2
STATE_ADD_UNITARIA = 3

# Banco de dados local (pode ser substituído por SQLite/Postgres depois)
DB_FILE = "bot_db.json"

def load_db():
    try:
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {"users": {}}

def save_db(db):
    with open(DB_FILE, 'w') as f:
        json.dump(db, f, indent=2)

# Inicializa DB
if not __import__('os').path.exists(DB_FILE):
    save_db({"users": {}})

db = load_db()

# ======================== LOGGING ========================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ======================== HELPERS GLADEPAY ========================
def gerar_pix(amount, client_ref=None):
    """Gera PIX via GladePay"""
    if not client_ref:
        client_ref = f"pix-{uuid.uuid4().hex[:8]}"
    
    headers = {
        "X-API-Key": GLADE_API_KEY,
        "Content-Type": "application/json"
    }
    
    payload = {
        "amount": float(amount),
        "clientReference": client_ref
    }
    
    try:
        response = requests.post(f"{BASE_URL}/pix/create", headers=headers, json=payload, timeout=10)
        data = response.json()
        
        if response.status_code == 201 or data.get("status") == "PENDING":
            return {
                "success": True,
                "qr_code_url": data.get("qrCodeUrl"),
                "qr_code_text": data.get("qrCodeText"),
                "expires_in": data.get("expiresIn", 3600),
                "transaction_id": data.get("id")
            }
        else:
            return {"success": False, "error": data.get("message", "Erro ao gerar PIX")}
            
    except Exception as e:
        return {"success": False, "error": str(e)}

def verificar_status_pix(transaction_id):
    """Verifica status de uma transação PIX"""
    headers = {"X-API-Key": GLADE_API_KEY}
    try:
        response = requests.get(f"{BASE_URL}/transactions/{transaction_id}", headers=headers, timeout=10)
        data = response.json()
        
        if response.status_code == 200:
            return {
                "success": True,
                "status": data.get("status"),
                "amount": data.get("amount"),
                "net_amount": data.get("netAmount"),
                "fee": data.get("fee")
            }
        else:
            return {"success": False, "error": data.get("message", "Erro ao consultar transação")}
            
    except Exception as e:
        return {"success": False, "error": str(e)}

# ======================== FUNÇÕES DE VERIFICAÇÃO CHK ========================
def verificar_chekagem(bin_data):
    """Verifica se um bin foi aprovado no CHK"""
    try:
        response = requests.post(CHK_URL, json={"bin": bin_data}, timeout=10)
        data = response.json()
        return data.get("approved", False)
    except Exception as e:
        logger.error(f"Erro ao verificar CHK: {e}")
        return False

# ======================== COMANDOS PRINCIPAIS ========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    if user_id not in db["users"]:
        db["users"][user_id] = {
            "balance": 0.0,
            "points": 0,
            "bins": [],
            "levels": [],
            "products": []
        }
        save_db(db)
    
    await mostrar_carteira(update, context)

async def mostrar_carteira(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user = db["users"].get(user_id, {})
    
    balance = user.get("balance", 0.0)
    points = user.get("points", 0)
    value_points = (points / 10) * 5  # 10 pontos = R$ 5,00
    
    message = f"""
👤 *Carteira*
━━━━━━━━━━━━━━━
🆔 ID: {user_id}
💰 Saldo: R$ {balance:.2f}
⭐ Pontos: {points} (~R$ {value_points:.2f})
━━━━━━━━━━━━━━━
💡 Dica: Ganhe 50% em pontos a cada compra!
    """
    
    keyboard = [
        [InlineKeyboardButton("➕ Adicionar Saldo", callback_data="deposito")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, parse_mode="Markdown", reply_markup=reply_markup)

async def deposito(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    # Gera PIX para adicionar saldo
    pix_data = gerar_pix(10.0, client_ref=f"saldo-{user_id}")
    
    if pix_data["success"]:
        message = f"""
💳 *Adicionar Saldo*
━━━━━━━━━━━━━━━
Valor: R$ 10,00
QR Code: {pix_data['qr_code_url']}
Código Copia e Cola:
<code>{pix_data['qr_code_text']}</code>
━━━━━━━━━━━━━━━
⏱️ Expira em: {pix_data['expires_in'] // 60} minutos
        """
        
        keyboard = [
            [InlineKeyboardButton("✅ Já fiz o pagamento", callback_data="verificar_pagamento")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(message, parse_mode="HTML", reply_markup=reply_markup)
        
        # Verifica status do PIX a cada 30 segundos
        transaction_id = pix_data["transaction_id"]
        check_payment_task(context, transaction_id, user_id)
    else:
        await update.message.reply_text(f"❌ Erro ao gerar PIX: {pix_data['error']}")

async def verificar_pagamento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Aqui você pode implementar a verificação manual ou automática
    await query.edit_message_text("Verificando pagamento...")
    
    # Simula verificação (substitua pela lógica real)
    import random
    if random.choice([True, False]):
        user_id = str(update.effective_user.id)
        db["users"][user_id]["balance"] += 10.0
        save_db(db)
        await query.edit_message_text("✅ Pagamento confirmado! Saldo adicionado.")
        await mostrar_carteira(update, context)
    else:
        await query.edit_message_text("⏳ Aguardando confirmação do banco...")

async def comprar_produto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user = db["users"].get(user_id, {})
    
    # Mostra lista de produtos
    products = [
        {"name": "GOLD", "price": 15},
        {"name": "CLASSIC", "price": 25},
        {"name": "PLATINUM", "price": 50},
        {"name": "STANDART", "price": 30},
        {"name": "PREPAID", "price": 35},
        {"name": "NU GOLD", "price": 15},
        {"name": "INFINITE", "price": 70},
        {"name": "BLACK", "price": 60},
        {"name": "BUSINESS", "price": 60},
        {"name": "ELO", "price": 70},
        {"name": "ELO BLACK", "price": 50},
        {"name": "MAESTRO", "price": 100},
        {"name": "NANJING", "price": 70},
        {"name": "CORPORATE", "price": 70},
        {"name": "VALE", "price": 25},
        {"name": "GRINGA", "price": 50},
        {"name": "ELECTRON", "price": 60},
        {"name": "GRAPHITE", "price": 60},
        {"name": "EXECUTIVE", "price": 60},
        {"name": "TITANIUM", "price": 70},
        {"name": "NU PLATINUM", "price": 25},
        {"name": "BLACK GRINGA", "price": 80},
        {"name": "NU BLACK", "price": 80},
        {"name": "ELO GRAPHITE", "price": 60},
        {"name": "PREPAID GRINGA", "price": 20},
        {"name": "VALE ALIMENTAÇÃO", "price": 5},
    ]
    
    message = "🛒 *Escolha um Produto*\n\n"
    for i, product in enumerate(products, 1):
        message += f"{i}. {product['name']} - R$ {product['price']}\n"
    
    keyboard = [[InlineKeyboardButton(product["name"], callback_data=f"comprar_{product['name']}")] for product in products]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(message, parse_mode="Markdown", reply_markup=reply_markup)

async def process_compra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    product_name = query.data.replace("comprar_", "")
    user_id = str(update.effective_user.id)
    user = db["users"].get(user_id, {})
    
    # Encontra o produto
    product = next((p for p in PRODUTOS if p["name"] == product_name), None)
    if not product:
        await query.edit_message_text("❌ Produto não encontrado.")
        return
    
    price = product["price"]
    if user["balance"] < price:
        await query.edit_message_text(f"❌ Saldo insuficiente. Você precisa de R$ {price:.2f} para comprar {product_name}.")
        return
    
    # Desconta o saldo
    user["balance"] -= price
    
    # Calcula pontos (50% do valor gasto convertido em pontos)
    points_earned = int((price * 0.5) / 5 * 10)  # 50% do valor, convertido em pontos (10 pontos = R$ 5,00)
    user["points"] += points_earned
    
    # Adiciona o produto à lista do usuário
    user["products"].append({
        "name": product_name,
        "price": price,
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "aprovado"  # Pode ser alterado após verificação CHK
    })
    
    # Atualiza o banco de dados
    db["users"][user_id] = user
    save_db(db)
    
    # Verifica no CHK se necessário
    if product_name in ["GOLD", "PLATINUM", "BLACK"]:  # Exemplo de produtos que precisam de verificação
        is_approved = verificar_chekagem(product_name)
        if is_approved:
            user["products"][-1]["status"] = "aprovado"
        else:
            user["products"][-1]["status"] = "reprovado"
    
    # Notifica o usuário
    message = f"""
✅ *Compra Realizada!*
━━━━━━━━━━━━━━━
Produto: {product_name}
Valor: R$ {price:.2f}
Pontos ganhos: {points_earned}
Status: {user['products'][-1]['status']}
━━━━━━━━━━━━━━━
💰 Novo saldo: R$ {user['balance']:.2f}
⭐ Novos pontos: {user['points']}
    """
    
    keyboard = [
        [InlineKeyboardButton("🔄 Continuar Comprando", callback_data="comprar_produto")],
        [InlineKeyboardButton("👛 Ver Carteira", callback_data="mostrar_carteira")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, parse_mode="Markdown", reply_markup=reply_markup)

# ======================== COMANDOS DE ADMINISTRAÇÃO ========================
async def add_binlevel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id != "SEU_USER_ID":  # Substitua pelo seu ID
        await update.message.reply_text("❌ Permissão negada.")
        return
    
    context.user_data['state'] = STATE_ADD_BINLEVEL
    await update.message.reply_text("Envie os bins e levels separados por vírgula:")

async def add_unitaria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id != "SEU_USER_ID":  # Substitua pelo seu ID
        await update.message.reply_text("❌ Permissão negada.")
        return
    
    context.user_data['state'] = STATE_ADD_UNITARIA
    await update.message.reply_text("Envie o nome e preço do produto separado por vírgula:")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('state')
    if state == STATE_ADD_BINLEVEL:
        # Processa os bins e levels
        bins_and_levels = update.message.text.split(",")
        for item in bins_and_levels:
            db["users"][str(update.effective_user.id)]["bins"].append(item.strip())
        save_db(db)
        await update.message.reply_text(f"✅ {len(bins_and_levels)} bins/levels adicionados.")
        context.user_data['state'] = None
    elif state == STATE_ADD_UNITARIA:
        # Processa o produto unitário
        parts = update.message.text.split(",")
        if len(parts) == 2:
            name, price = parts[0].strip(), float(parts[1].strip())
            PRODUTOS.append({"name": name, "price": price})
            save_db(db)
            await update.message.reply_text(f"✅ Produto '{name}' adicionado com sucesso.")
        else:
            await update.message.reply_text("❌ Formato inválido. Use: nome,preço")
        context.user_data['state'] = None

# ======================== CALLBACK HANDLERS ========================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    callback_data = query.data
    
    if callback_data == "deposito":
        await deposito(update, context)
    elif callback_data == "verificar_pagamento":
        await verificar_pagamento(update, context)
    elif callback_data == "comprar_produto":
        await comprar_produto(update, context)
    elif callback_data.startswith("comprar_"):
        await process_compra(update, context)
    elif callback_data == "mostrar_carteira":
        await mostrar_carteira(update, context)

# ======================== MAIN ========================
def main():
    # Substitua pelo token do seu bot
    TOKEN = "SEU_TOKEN_AQUI"
    
    application = Application.builder().token(TOKEN).build()
    
    # Comandos principais
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("carteira", mostrar_carteira))
    application.add_handler(CommandHandler("pix", deposito))
    application.add_handler(CommandHandler("addbinlevel", add_binlevel))
    application.add_handler(CommandHandler("addunitaria", add_unitaria))
    
    # Mensagens gerais
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Callback handlers
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    logger.info("Bot iniciado!")
    application.run_polling()

if __name__ == "__main__":
    main()
