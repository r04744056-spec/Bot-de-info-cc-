import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
import os
import json
import time
import random

# CONFIGURAÇÃO DA API
API_TOKEN = "9154649902:AAFYHPeK2rroBfnfLKW6S2tb9wiSKPk9e34"  # Substitua pelo token do BotFather
ADMIN_ID = 123456789          # Substitua pelo seu ID numérico do Telegram

# Estrutura de dados (Simulando um banco de dados local)
# Estrutura: { bin: { bank: "", level: "", stock: 0, price: 0 } }
DATA_FILE = "cc_data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=4)

DATA = load_data()

# --- FUNÇÕES AUXILIARES ---

def is_admin(user_id):
    return user_id == ADMIN_ID

def check_live_status(bin_info):
    """Simula uma verificação se o cartão está 'Live' ou não."""
    # Na prática, aqui você chamaria sua API de chk externa
    # Vamos simular com uma chance aleatória para teste
    chance = random.randint(1, 100)
    if chance > 30: # 70% de chance de ser live
        return True, "LIVE ✅"
    else:
        return False, "DEAD ❌"

def format_card(card):
    bin_code = card['bin']
    info = f"📦 *BIN:* `{bin_code}`\n"
    info += f"🏦 *Banco:* {card.get('bank', 'N/A')}\n"
    info += f"💎 *Level:* {card.get('level', 'Standard')}\n"
    info += f"💰 *Preço:* ${card.get('price', '0.00')}\n"
    
    status_icon, status_text = check_live_status(card)
    info += f"⚡ *Status:* {status_text}"
    
    return info, status_icon

# --- HANDLERS DO BOT ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Comprar Cartão", callback_data='comprar_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👋 Bem-vindo ao Loja de Cards!\n\n"
        "Selecione uma opção abaixo:",
        reply_markup=reply_markup
    )

async def comprar_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Agrupar cards por nível
    levels = set()
    for bin_code, info in DATA.items():
        levels.add(info.get('level', 'Standard'))
        
    buttons = []
    for level in sorted(list(levels)):
        buttons.append([InlineKeyboardButton(f"Nível: {level}", callback_data=f'lvl_{level}')])
        
    buttons.append([InlineKeyboardButton("Voltar", callback_data='back_start')])
    
    await query.edit_message_text(
        "Escolha o nível do cartão:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def show_level_cards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    selected_level = query.data.replace('lvl_', '')
    matching_cards = {k: v for k, v in DATA.items() if v.get('level') == selected_level}
    
    if not matching_cards:
        await query.edit_message_text(f"Nenhum cartão encontrado para o nível: {selected_level}")
        return

    # Mostrar apenas UM exemplo de cada nível (ou todos desse nível específico)
    text = f"*Cartões disponíveis - Nível {selected_level}:*\n\n"
    buttons = []
    
    for bin_code, info in matching_cards.items():
        formatted_text, icon = format_card(info)
        text += f"{formatted_text}\n"
        buttons.append([InlineKeyboardButton(f"Comprar Bin {bin_code}", callback_data=f'buy_{bin_code}')])
        
    buttons.append([InlineKeyboardButton("Voltar", callback_data='comprar_menu')])
    
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

async def buy_card_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    bin_code = query.data.replace('buy_', '')
    if bin_code in DATA:
        card_info = DATA[bin_code]
        formatted_text, icon = format_card(card_info)
        await query.edit_message_text(f"Detalhes da Compra:\n\n{formatted_text}")
    else:
        await query.edit_message_text("Card não encontrado.")

async def cmd_bin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Use: /bin <codigo_da_bin>")
        return
        
    bin_code = args[0]
    if bin_code in DATA:
        card_info = DATA[bin_code]
        formatted_text, icon = format_card(card_info)
        
        has_stock = int(card_info.get('stock', 0)) > 0
        
        if has_stock:
            keyboard = [[InlineKeyboardButton("Comprar Agora 🛒", callback_data=f'buy_{bin_code}')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(formatted_text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(f"{icon} {formatted_text}\n\n❌ *Sem Estoque*")
    else:
        await update.message.reply_text("Bin não encontrada na base de dados.")

async def cmd_banco(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Use: /banco <nome_do_banco>")
        return
        
    banco_nome = args[0].capitalize()
    matching = {k: v for k, v in DATA.items() if banco_nome.lower() in v.get('bank', '').lower()}
    
    if not matching:
        await update.message.reply_text(f"Nenhum cartão do banco '{banco_nome}' encontrado.")
        return
        
    msg = f"*Cartões do Banco: {banco_nome}*\n\n"
    for bin_code, info in matching.items():
        formatted_text, _ = format_card(info)
        msg += f"{formatted_text}\n"
        
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Use: /level <nivel> (ex: Gold, Standard)")
        return
        
    nivel = args[0].capitalize()
    matching = {k: v for k, v in DATA.items() if v.get('level') == nivel}
    
    if not matching:
        await update.message.reply_text(f"Nenhum cartão no nível '{nivel}' encontrado.")
        return
        
    msg = f"*Cartões Nível: {nivel}*\n\n"
    for bin_code, info in matching.items():
        formatted_text, _ = format_card(info)
        msg += f"{formatted_text}\n"
        
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_add_produto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Apenas o administrador pode adicionar produtos.")
        return
    
    # Exemplo simples: /addproduto bin banco level preco
    args = context.args
    if len(args) < 4:
        await update.message.reply_text("Formato: /addproduto <bin> <banco> <level> <preco>")
        return
        
    bin_code, banco, level, preco = args
    DATA[bin_code] = {
        'bin': bin_code,
        'bank': banco,
        'level': level,
        'price': preco,
        'stock': 0 # Inicia sem estoque, use /addestoque depois
    }
    save_data(DATA)
    await update.message.reply_text(f"Produto adicionado: {bin_code}")

async def cmd_add_estoque(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Apenas o administrador pode gerenciar estoque.")
        return
        
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Formato: /addestoque <bin> <quantidade>")
        return
        
    bin_code, qtd_str = args
    try:
        qtd = int(qtd_str)
        if bin_code in DATA:
            current_stock = int(DATA[bin_code].get('stock', 0))
            DATA[bin_code]['stock'] = current_stock + qtd
            save_data(DATA)
            await update.message.reply_text(f"Estoque atualizado! Novo total: {DATA[bin_code]['stock']}")
        else:
            await update.message.reply_text("Bin não encontrada.")
    except ValueError:
        await update.message.reply_text("Quantidade inválida.")

async def cmd_estoque(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Apenas o administrador pode ver o estoque.")
        return
        
    msg = "*Relatório de Estoque Completo:*\n\n"
    for bin_code, info in DATA.items():
        stock = info.get('stock', 0)
        bank = info.get('bank', 'N/A')
        level = info.get('level', 'Standard')
        msg += f"• Bin: {bin_code} | Banco: {bank} | Level: {level} | Estoque: {stock}\n"
        
    await update.message.reply_text(msg, parse_mode="Markdown")

# --- INICIALIZAÇÃO ---

def main():
    app = ApplicationBuilder().token(API_TOKEN).build()
    
    # Handlers de Comando
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("bin", cmd_bin))
    app.add_handler(CommandHandler("banco", cmd_banco))
    app.add_handler(CommandHandler("level", cmd_level))
    app.add_handler(CommandHandler("addproduto", cmd_add_produto))
    app.add_handler(CommandHandler("addestoque", cmd_add_estoque))
    app.add_handler(CommandHandler("estoque", cmd_estoque))
    
    # Handlers de Botão (Callback)
    app.add_handler(CallbackQueryHandler(comprar_menu, pattern='^comprar_menu$'))
    app.add_handler(CallbackQueryHandler(show_level_cards, pattern='^lvl_'))
    app.add_handler(CallbackQueryHandler(buy_card_callback, pattern='^buy_'))
    app.add_handler(CallbackQueryHandler(start, pattern='^back_start$'))
    
    print("Bot iniciado...")
    app.run_polling()

if __name__ == '__main__':
    main()
