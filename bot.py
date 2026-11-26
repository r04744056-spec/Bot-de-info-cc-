import logging
import requests
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler

# --- CONFIGURAÇÕES ---
BOT_TOKEN = "BOT_TOKEN" # Coloque seu token do BotFather aqui
ADMIN_ID = "ADMIN_ID"        # SEU ID NUMÉRICO AQUI

# URLs dos serviços
CHK_URL = "http://143.20.50.71/cielo/"
GLADE_API = "GLADD_API"
GLADE_BASE = "https://web-production-73dbd.up.railway.app/api/v1"

# Simulação de Banco de Dados
user_db = {} 
gift_database = {}
products_db = []

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

def get_user(user_id):
    if user_id not in user_db:
        user_db[user_id] = {"balance": 0.0, "used_gifts": []}
    return user_db[user_id]

def check_bin_on_chk(bin_number):
    """
    Tenta verificar a bin na sua URL do CHK.
    """
    try:
        response = requests.get(f"{CHK_URL}{bin_number}", timeout=5)
        # Ajuste conforme o retorno real do seu CHK
        if response.status_code == 200:
            return True, "Live" 
        else:
            return False, "Offline"
    except Exception as e:
        logger.error(f"Erro CHK: {e}")
        return False, "Erro de conexão"

# --- COMANDOS ADMIN ---

async def admin_generate_gift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Acesso negado.")
        return
    
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Use: /admin_gift CODIGO VALOR")
        return
        
    code = args[0].upper()
    try:
        value = float(args[1])
    except ValueError:
        await update.message.reply_text("Valor inválido.")
        return
        
    gift_database[code] = {"value": value, "used_by": []}
    await update.message.reply_text(f"✅ Gift {code} criado: R$ {value}")

async def admin_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Acesso negado.")
        return
        
    args = context.args
    if len(args) < 3:
        await update.message.reply_text("Use: /admin_prod NOME PREÇO DESC")
        return
        
    name = " ".join(args[:-2]) 
    price = float(args[-2])
    desc = args[-1]
    
    new_id = max([p['id'] for p in products_db], default=0) + 1
    products_db.append({"id": new_id, "name": name, "price": price, "desc": desc})
    
    await update.message.reply_text(f"✅ Produto '{name}' adicionado por R$ {price}")

# --- COMANDOS USUÁRIO ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    msg = (f"👋 *Carteira Ativa*\n\n💰 Saldo: R$ {user['balance']:.2f}\n\n"
           "Selecione uma opção:")
           
    kb = [
        [InlineKeyboardButton("🎁 Resgatar Gift", callback_data='resg')],
        [InlineKeyboardButton("🛒 Comprar Produto", callback_data='buy')],
        [InlineKeyboardButton("🔍 Consultar BIN", callback_data='chk')]
    ]
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))

async def resgatar_gift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Envie o código: /resgatar CODIGO")
        return
        
    code = args[0].upper()
    user_id = update.effective_user.id
    
    if code not in gift_database:
        await update.message.reply_text("❌ Código inválido.")
        return
        
    gift = gift_database[code]
    if user_id in gift["used_by"]:
        await update.message.reply_text("❌ Já utilizado.")
        return
        
    gift["used_by"].append(user_id)
    get_user(user_id)["balance"] += gift["value"]
    
    await update.message.reply_text(f"✅ +R$ {gift['value']:.2f} adicionado ao saldo!")

async def buy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not products_db:
        await update.callback_query.answer()
        await update.effective_message.edit_text("Nenhum produto disponível.")
        return
        
    kb = []
    for p in products_db:
        kb.append([InlineKeyboardButton(f"{p['name']} (R$ {p['price']})", callback_data=f'prod_{p["id"]}')])
    kb.append([InlineKeyboardButton("Voltar", callback_data='back')])
    
    await update.callback_query.answer()
    text = "Escolha um produto:"
    await update.effective_message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def chk_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.effective_message.edit_text("Digite a BIN (ex: 411111):")
    # Em produção, use ConversationHandler para capturar o texto do usuário

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    await query.answer()
    
    if data == 'resg':
        await query.edit_message_text("Envie o código após /resgatar CODIGO")
        
    elif data == 'buy':
        await buy_handler(update, context)
        
    elif data == 'chk':
        await chk_handler(update, context)
        await query.edit_message_text("Digite a BIN agora (ex: 411111)")
        
    elif data == 'back':
        await start(update, context)
        
    elif data.startswith('prod_'):
        prod_id = int(data.split('_')[1])
        prod = next((p for p in products_db if p['id'] == prod_id), None)
        if not prod: return
        
        await query.edit_message_text(f"Verificando BIN para {prod['name']}...")
        
        # Verificação da BIN
        is_live, status = check_bin_on_chk("411111") 
        
        if is_live:
            try:
                payload = {"amount": prod['price'], "clientReference": f"tx_{user_id}_{prod_id}"}
                headers = {"X-API-Key": GLADE_API_KEY}
                resp = requests.post(f"{GLADE_BASE_URL}/pix/create", json=payload, headers=headers)
                
                if resp.status_code == 201:
                    qr = resp.json().get('qrCodeUrl', '')
                    new_bal = user['balance'] - prod['price']
                    user['balance'] = new_bal
                    
                    await query.edit_message_text(f"✅ Compra Aprovada!\nBin: Live\nSaldo Restante: R$ {new_bal:.2f}")
                    await context.bot.send_photo(query.message.chat_id, photo=qr, caption=f"QR Code GladePay: {prod['name']}")
                else:
                    await query.edit_message_text("❌ Erro ao gerar PIX.")
            except Exception as e:
                await query.edit_message_text(f"Erro Glade: {e}")
        else:
            await query.edit_message_text(f"❌ BIN não Live ({status}). Compra cancelada.")

def main():
    # Método moderno compatível com v21+ e Python 3.13
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("resgatar", resgatar_gift))
    app.add_handler(CommandHandler("admin_gift", admin_generate_gift))
    app.add_handler(CommandHandler("admin_prod", admin_add_product))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    app.run_polling()

if __name__ == '__main__':
    main()
