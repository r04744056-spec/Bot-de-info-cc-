import logging
import requests
import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler

# Configuração de Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- CONFIGURAÇÕES GERAIS ---
BOT_TOKEN = os.getenv('BOT_TOKEN') # Coloque seu token aqui ou use env vars
GLADE_API_KEY = os.getenv('GLADE_API_KEY')
GLADE_BASE_URL = "https://web-production-73dbd.up.railway.app/api/v1"
CHK_URL = "http://143.20.50.71/cielo/" 

# SEU ID DE ADMINISTRADOR (Substitua pelo seu número real)
ADMIN_ID = 123456789 

# Simulação de Banco de Dados Local
# Estrutura: { user_id: { "balance": float, "gift_codes_used": list } }
user_db = {}

# Banco de dados de Gifts válidos (simulado)
# Em produção, salve isso em um arquivo JSON ou banco de dados real
gift_database = {
    "GIFT10": {"value": 10.0, "used_by": []},
    "GIFT100": {"value": 100.0, "used_by": []}
}

# Banco de dados de Produtos/Infos
products_db = [
    {"id": 1, "name": "Info Premium", "price": 5.00, "description": "Dados completos"},
    {"id": 2, "name": "Bin Live", "price": 2.50, "description": "Verificação rápida"}
]

def get_user_data(user_id):
    if user_id not in user_db:
        user_db[user_id] = {"balance": 0.0, "gift_codes_used": []}
    return user_db[user_id]

def update_balance(user_id, amount):
    data = get_user_data(user_id)
    data["balance"] += amount
    return data["balance"]

def check_bin_live(bin_number):
    """
    Verifica no seu CHK se a bin está Live.
    Aqui simulamos a lógica baseada na sua URL.
    """
    # Exemplo simples: Bins começando com 4 são Visa (simulando que estão Live)
    if bin_number.startswith("4"):
        return True, "Visa - Live"
    elif bin_number.startswith("5"):
        return True, "Mastercard - Live"
    else:
        return False, "Bin não encontrada ou Offline"

# --- COMANDOS DO ADMIN ---

async def admin_generate_gift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando: /admin_gift CODIGO VALOR
    Ex: /admin_gift MEUGRANDE 50
    """
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Você não é administrador.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Uso: /admin_gift CODIGO VALOR\nEx: /admin_gift NOVO50 50")
        return

    code = args[0].upper()
    try:
        value = float(args[1])
    except ValueError:
        await update.message.reply_text("O valor deve ser um número.")
        return

    gift_database[code] = {"value": value, "used_by": []}
    
    await update.message.reply_text(f"✅ Gift `{code}` criado com valor de R$ {value:.2f}", parse_mode='Markdown')

async def admin_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando: /admin_prod NOME PREÇO DESCRIÇÃO
    Ex: /admin_prod CC LEEVA 10.00 Info completa
    """
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Você não é administrador.")
        return

    args = context.args
    if len(args) < 3:
        await update.message.reply_text("Uso: /admin_prod NOME PREÇO DESCRICAO\nEx: /admin_prod CC LEEVA 10.00 Info completa")
        return

    name = args[0] + " " + args[1] # Junta as duas primeiras palavras como nome
    try:
        price = float(args[2])
    except ValueError:
        await update.message.reply_text("O preço deve ser um número.")
        return
    
    description = " ".join(args[3:]) if len(args) > 3 else "Sem descrição"

    new_id = max([p['id'] for p in products_db], default=0) + 1
    products_db.append({
        "id": new_id,
        "name": name,
        "price": price,
        "description": description
    })

    await update.message.reply_text(f"✅ Produto '{name}' adicionado por R$ {price:.2f}")

# --- COMANDOS USUÁRIO ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = get_user_data(user_id)
    
    welcome_msg = (
        f"👋 *Bem-vindo à Carteira*\n\n"
        f"💰 *Saldo Atual:* R$ {data['balance']:.2f}\n\n"
        "Escolha uma opção:\n"
        "1️⃣ Resgatar Gift\n"
        "2️⃣ Comprar Produto (Usando Saldo)\n"
        "3️⃣ Consultar Bin (CHK)"
    )
    
    keyboard = [
        [InlineKeyboardButton("🎁 Resgatar Gift", callback_data='resgatar_gift')],
        [InlineKeyboardButton("🛒 Comprar com Saldo", callback_data='comprar_saldo')],
        [InlineKeyboardButton("🔍 Consultar Bin", callback_data='consultar_bin')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(welcome_msg, parse_mode='Markdown', reply_markup=reply_markup)

async def resgatar_gift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /resgatar CODIGO"""
    args = context.args
    if not args:
        await update.message.reply_text("Envie o código após /resgatar.\nEx: /resgatar GIFT123")
        return
    
    code = args[0].upper()
    user_id = update.effective_user.id
    
    if code not in gift_database:
        await update.message.reply_text("❌ Código inválido.")
        return
        
    gift_info = gift_database[code]
    
    if user_id in gift_info["used_by"]:
        await update.message.reply_text("❌ Você já usou este código.")
        return
    
    valor = gift_info["value"]
    new_balance = update_balance(user_id, valor)
    gift_info["used_by"].append(user_id)
    
    await update.message.reply_text(
        f"✅ *Gift Resgatado!*\n\n"
        f"💰 Valor adicionado: R$ {valor:.2f}\n"
        f"💵 Novo Saldo: R$ {new_balance:.2f}"
    )

async def comprar_com_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia fluxo de compra usando saldo da carteira"""
    user_id = update.effective_user.id
    data = get_user_data(user_id)
    
    if not products_db:
        await update.callback_query.answer()
        await update.effective_message.edit_text("⚠️ Nenhum produto disponível no momento.")
        return

    # Lista produtos
    prod_list = "\n".join([f"{i+1}. *{p['name']}* - R$ {p['price']:.2f}" for i, p in enumerate(products_db)])
    msg = f"🛒 *Produtos Disponíveis:*\n\n{prod_list}\n\nDigite o número do produto para comprar."
    
    await update.callback_query.answer()
    await update.effective_message.edit_text(msg, parse_mode='Markdown')
    
    # Nota: Em produção, use ConversationHandler para capturar o índice escolhido.
    # Aqui vamos simular que o usuário escolheu o primeiro produto (índice 0)
    # E pedimos o cartão para verificar no CHK.
    await processar_compra_por_saldo(update, products_db[0]["price"], "4111111111111111")

async def consultar_bin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Consulta bin no CHK"""
    await update.callback_query.answer()
    msg = "Digite o número inicial do cartão para verificar se está Live:\nEx: 4111"
    await update.effective_message.edit_text(msg)
    # Simula verificação de uma bin específica
    await verificar_e_mostrar_bin(update, "4111")

async def verificar_e_mostrar_bin(update: Update, bin_number):
    is_live, status = check_bin_live(bin_number)
    
    if is_live:
        text = f"✅ *BIN {bin_number} está LIVE!*\n\n{status}\n\nDeseja comprar?"
        keyboard = [[InlineKeyboardButton("Sim, Comprar", callback_data=f'buy_{bin_number}')]]
    else:
        text = f"❌ *BIN {bin_number} não está Live.*\n\n{Tried to verify but failed}"
        keyboard = [[InlineKeyboardButton("Tentar outra", callback_data='back_to_main')]]
        
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.effective_message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)

async def processar_compra_por_saldo(update, price, card_number):
    """Processa a compra descontando do saldo e verificando no CHK"""
    bin_part = card_number[:6]
    is_live, status = check_bin_live(bin_part)
    
    if not is_live:
        await update.effective_message.reply_text(f"❌ A bin {bin_part} não está Live no CHK. Compra cancelada.")
        return

    user_id = update.effective_user.id
    data = get_user_data(user_id)
    
    if data["balance"] >= price:
        new_balance = update_balance(user_id, -price)
        
        # Gera o PIX na GladePay para finalizar a transação
        try:
            payload = {
                "amount": price,
                "clientReference": f"saldo_{user_id}_{int(context.bot.last_updated_time.timestamp())}"
            }
            headers = {"X-API-Key": GLADE_API_KEY, "Content-Type": "application/json"}
            response = requests.post(f"{GLADE_BASE_URL}/pix/create", headers=headers, json=payload)
            
            if response.status_code == 201:
                qr_url = response.json().get('qrCodeUrl', '')
                await update.effective_message.reply_photo(
                    photo=qr_url,
                    caption=f"✅ *Compra Aprovada!*\n\n"
                            f"💳 Bin: {bin_part} (Live)\n"
                            f"💰 Valor: R$ {price:.2f}\n"
                            f"💵 Saldo Restante: R$ {new_balance:.2f}\n\n"
                            f"QR Code gerado pela GladePay."
                )
            else:
                await update.effective_message.reply_text("❌ Erro ao gerar QR Code na GladePay.")
                
        except Exception as e:
            logger.error(f"Erro Glade: {e}")
            await update.effective_message.reply_text("❌ Erro de conexão com a gateway.")
    else:
        await update.effective_message.reply_text("❌ Saldo insuficiente para esta compra.")

# --- HANDLERS DE CALLBACK ---

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data == 'resgatar_gift':
        await update.callback_query.answer()
        await update.effective_message.edit_text("Envie o código do gift após /resgatar.")
        # Aqui você redirecionaria para o handler de texto para capturar o código
        # Mas para simplificar, vamos voltar ao menu principal se quiser
        await start(update, context)
        
    elif data == 'comprar_saldo':
        await update.callback_query.answer()
        await comprar_com_saldo(update, context)
        
    elif data == 'consultar_bin':
        await update.callback_query.answer()
        await consultar_bin(update, context)
        
    elif data == 'back_to_main':
        await update.callback_query.answer()
        await start(update, context)

    elif data.startswith('buy_'):
        bin_num = data.split('_')[1]
        await update.callback_query.answer()
        await verificar_e_mostrar_bin(update, bin_num)

# --- INICIALIZAÇÃO ---

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Handlers de Comando
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("resgatar", resgatar_gift))
    
    # Handlers de Admin
    app.add_handler(CommandHandler("admin_gift", admin_generate_gift))
    app.add_handler(CommandHandler("admin_prod", admin_add_product))
    
    # Handlers de Callback (Botões)
    app.add_handler(CallbackQueryHandler(button_handler))
    
    app.run_polling()

if __name__ == '__main__':
    main()j
