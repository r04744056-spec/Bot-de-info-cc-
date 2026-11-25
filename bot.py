import asyncio, os, re, secrets, sqlite3, threading, time, random, io, json, string
from dotenv import load_dotenv
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, InputMediaPhoto, MessageEntity
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, ConversationHandler, MessageHandler, filters)

load_dotenv()
TOKEN = os.environ.get("BOT_TOKEN", "SEU_TOKEN_AQUI")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "SEU_ID_AQUI"))
DB_PATH = os.environ.get("DB_PATH", "/data/shop.db")
GLADE_BASE = os.environ.get("GLADE_BASE", "https://web-production-73dbd.up.railway.app/api/v1")
GLADE_KEY = os.environ.get("GLADE_KEY", "SUA_CHAVE_API_AQUI")
BINLIST_API = "https://www.binlist.net" 

# Configurações globais
LOCK = threading.RLock()

# --- BANCO DE DADOS ---
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT, balance REAL DEFAULT 0, created INTEGER);
CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, delta REAL, reason TEXT, ref TEXT, at INTEGER);
CREATE TABLE IF NOT EXISTS invoices(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL, ref TEXT UNIQUE, status TEXT DEFAULT 'pending', at INTEGER);
CREATE TABLE IF NOT EXISTS cards(id INTEGER PRIMARY KEY AUTOINCREMENT, num TEXT, mm TEXT, yy TEXT, cvv TEXT, holder TEXT, bin TEXT, brand TEXT, level TEXT, bank TEXT, price REAL, status TEXT DEFAULT 'live', sold_to INTEGER, created INTEGER);

-- TABELA PARA GIFT CARDS
CREATE TABLE IF NOT EXISTS gift_cards(
    id INTEGER PRIMARY KEY AUTOINCREMENT, 
    code TEXT UNIQUE, 
    value REAL, 
    status TEXT DEFAULT 'active', 
    used_by INTEGER, 
    created_at INTEGER
);
"""

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def execute_query(sql, args=(), fetch_one=False):
    with LOCK:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(sql, args)
        if fetch_one:
            res = cur.fetchone()
        else:
            res = cur.fetchall()
        conn.commit()
        conn.close()
        return res

def init_db():
    for s in SCHEMA.split(";"):
        if s.strip():
            try:
                execute_query(s)
            except: pass

# --- FUNÇÕES AUXILIARES ---
def format_money(val):
    return f"R$ {val:.2f}".replace('.', ',')

def generate_code(length=12):
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=length))

def luhn_check(num_str):
    digits = [int(d) for d in str(num_str)]
    checksum = sum(digits[-1::-2]) + sum(sum(divmod(2 * d, 10)) for d in digits[-2::-2])
    return checksum % 10 == 0

async def fetch_bin_info(bin_num):
    try:
        url = f"{BINLIST_API}/lookup/{bin_num}.json"
        r = requests.get(url, timeout=5)
        data = r.json()
        if data.get('bank'):
            return {
                'brand': data.get('scheme', 'Unknown'),
                'level': data['bank'].get('website_url', '').split('/')[-1] if data['bank'].get('website_url') else 'Standard',
                'bank': data['bank'].get('name', 'Unknown'),
                'country': data.get('country', {}).get('alpha2', '??')
            }
    except: pass
    return {'brand': 'Unknown', 'level': 'Standard', 'bank': 'Unknown', 'country': '??'}

def parse_card_line(line):
    parts = [p.strip() for p in line.replace(';', '|').split('|')]
    if len(parts) < 4: return None
    
    num = re.sub(r'\D', '', parts[0])
    if not luhn_check(num): return None
    
    mm = parts[1].zfill(2) if len(parts) > 1 else "00"
    yy = parts[2][-2:] if len(parts) > 2 else "00"
    cvv = parts[3] if len(parts) > 3 else "000"
    holder = parts[4] if len(parts) > 4 else ""
    
    return {'num': num, 'mm': mm, 'yy': yy, 'cvv': cvv, 'holder': holder}

# --- HANDLERS DO BOT ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    execute_query("INSERT OR IGNORE INTO users(id, username, created) VALUES (?, ?, ?)", 
                  (user.id, user.username or "", int(time.time())))
    
    kb = [
        [InlineKeyboardButton("🛒 Ver Cartões", callback_data="shop_main")],
        [InlineKeyboardButton("🎁 Resgatar Gift", callback_data="resgate_menu")]
    ]
    await update.message.reply_text(f"Olá {user.first_name}! Digite /help para ver todos os comandos.", reply_markup=kb)

async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = execute_query("SELECT bin, COUNT(*) as qtd, AVG(price) as avg_price FROM cards WHERE status='live' GROUP BY bin")
    
    if not rows:
        await update.message.reply_text("Não há cartões disponíveis.")
        return

    kb_rows = []
    for row in rows:
        bin_val, qty, avg_p = row
        info = await fetch_bin_info(bin_val[:6])
        
        btn_text = f"{info['brand']} - {info['bank']} ({qty}) - {format_money(avg_p)}"
        kb_rows.append([InlineKeyboardButton(btn_text, callback_data=f"buy_bin:{bin_val}")])
        
    kb = InlineKeyboardMarkup(kb_rows)
    await update.message.reply_text("Escolha um grupo de cartões:", reply_markup=kb)

async def buy_bin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    _, bin_val = query.data.split(":")
    cards = execute_query("SELECT id, num, mm, yy, cvv, price FROM cards WHERE bin=? AND status='live'", (bin_val,))
    
    if not cards:
        await query.edit_message_text("Cartões esgotados para este BIN.")
        return

    if len(cards) == 1:
        c = cards[0]
        await show_single_offer(query, c)
    else:
        total_price = sum(c[4] for c in cards)
        kb = [
            [InlineKeyboardButton(f"📦 Comprar Lote ({len(cards)}) - {format_money(total_price)}", callback_data=f"buy_batch:{bin_val}")],
            [InlineKeyboardButton("🔍 Ver Unitários", callback_data=f"show_unit:{bin_val}")]
        ]
        await query.edit_message_text(f"Grupo {bin_val}: {len(cards)} cartões disponíveis.", reply_markup=kb)

async def show_single_offer(query, card_data):
    cid, num, mm, yy, cvv, price = card_data
    kb = [
        [InlineKeyboardButton("💳 Comprar", callback_data=f"confirm_buy:{cid}")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="cancel_buy")]
    ]
    text = f"📦 **{num}|{mm}|{yy}|{cvv}**\n💰 Preço: {format_money(price)}"
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)

async def confirm_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    is_batch = ":batch" in data
    ref_id = data.split(":")[1]
    
    uid = query.from_user.id
    
    # 1. Buscar dados dos cartões ou lote
    if is_batch:
        cards = execute_query("SELECT id, num, mm, yy, cvv, price FROM cards WHERE bin=? AND status='live'", (ref_id,))
        total = sum(c[4] for c in cards)
        card_ids = [c[0] for c in cards]
        card_details = [(c[1], c[2], c[3], c[4]) for c in cards] # num, mm, yy, cvv
    else:
        cid = ref_id
        card = execute_query("SELECT id, num, mm, yy, cvv, price FROM cards WHERE id=? AND status='live'", (cid,), True)
        if not card:
            await query.edit_message_text("Cartão já vendido.")
            return
        total = card[5]
        card_ids = [card[0]]
        card_details = [(card[1], card[2], card[3], card[4])]

    # 2. Verificar Saldo do Usuário
    user_balance = execute_query("SELECT balance FROM users WHERE id=?", (uid,), True)[0]
    
    if user_balance >= total:
        # --- COMPRA VIA SALDO ---
        client_ref = f"BAL-{uid}-{int(time.time())}"
        
        # Debita saldo
        execute_query("UPDATE users SET balance = balance - ? WHERE id=?", (total, uid))
        
        # Marca cartões como vendidos
        for cid in card_ids:
            execute_query("UPDATE cards SET status='sold', sold_to=? WHERE id=?", (uid, cid))
            
        # Registra no histórico
        execute_query("INSERT INTO ledger(user_id, delta, reason, ref, at) VALUES(?,?,?,?,?)",
                      (uid, -total, "Compra Cartão (Saldo)", client_ref, int(time.time())))
                      
        txt = "✅ Pagamento via Saldo!\n\nSeus cartões:\n" + "\n".join(f"{c[0]}|{c[1]}|{c[2]}|{c[3]}" for c in card_details)
        await query.edit_message_text(txt)
        
    else:
        # --- COMPRA VIA PIX (GLADEPAY) ---
        client_ref = f"CC-{uid}-{int(time.time())}"
        
        headers = {"X-API-Key": GLADE_KEY, "Content-Type": "application/json"}
        payload = {"amount": total, "clientReference": client_ref}
        
        try:
            r = requests.post(f"{GLADE_BASE}/pix/create", json=payload, headers=headers, timeout=10)
            data_api = r.json()
            
            if r.status_code in [201, 200]:
                qr_url = data_api.get("qrCodeUrl", "")
                qr_text = data_api.get("qrCodeText", "")
                
                execute_query("INSERT INTO invoices(user_id, amount, ref, status, at) VALUES(?,?,?,?,?)",
                              (uid, total, client_ref, "pending", int(time.time())))
                
                img = InputMediaPhoto(media=qr_url, caption=(
                    f"📦 Pedido #{client_ref}\n"
                    f"Valor: {format_money(total)}\n\n"
                    f"Escaneie ou copie a chave:"
                ))
                
                kb = [[InlineKeyboardButton("Copiar Chave Pix", switch_inline_query_current_chat=qr_text)]]
                await query.edit_message_media(media=img, reply_markup=kb)
                
                asyncio.create_task(monitor_payment(client_ref, card_ids, card_details, query))
                
            else:
                await query.edit_message_text(f"Erro ao gerar PIX: {data_api}")
                
        except Exception as e:
            await query.edit_message_text(f"Erro: {e}")

async def monitor_payment(ref, card_ids, card_details, query_obj):
    time.sleep(5) 
    while True:
        try:
            r = requests.get(f"{GLADE_BASE}/transactions/{ref}", headers={"X-API-Key": GLADE_KEY}, timeout=5)
            tx = r.json()
            if tx.get('status') == 'COMPLETED':
                # Se chegou aqui, o PIX foi pago. Agora entregamos os cartões.
                for cid in card_ids:
                    execute_query("UPDATE cards SET status='sold', sold_to=? WHERE id=?", 
                                  (query_obj.from_user.id, cid))
                
                txt = "✅ Pagamento Confirmado!\n\nSeus cartões:\n" + "\n".join(f"{c[0]}|{c[1]}|{c[2]}|{c[3]}" for c in card_details)
                await query_obj.edit_message_text(txt)
                break
            elif tx.get('status') == 'FAILED':
                # Se falhou, devolvemos os cartões para o pool (opcional)
                # Por enquanto, apenas avisamos
                await query_obj.edit_message_text("Pagamento falhou. Os cartões estão livres novamente.")
                # Devolver ao pool seria: UPDATE cards SET status='live', sold_to=NULL WHERE id IN (...)
                break
            time.sleep(5)
        except:
            time.sleep(5)

# --- HANDLERS DE GIFT CARD ---

async def add_gift_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Apenas admin.")
        return ConversationHandler.END
        
    await update.message.reply_text("Digite o valor do Gift Card (ex: 10.00):")
    return 1

async def add_gift_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(update.message.text)
        code = generate_code()
        
        execute_query("INSERT INTO gift_cards(code, value, status, created_at) VALUES(?,?,?,?)",
                      (code, val, 'active', int(time.time())))
        
        msg = f"🎁 **Gift Card Gerado!**\n\nCódigo: `{code}`\nValor: {format_money(val)}\n\nEnvie isso para o cliente."
        await update.message.reply_text(msg, parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("Valor inválido. Tente novamente.")
        
    return ConversationHandler.END

async def resgate_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Para resgatar, envie o código aqui ou use /resgatar [CODIGO]")

async def resgate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = update.message.text.split()
    
    if len(args) < 2:
        await update.message.reply_text("Uso: /resgatar CODIGO")
        return

    code = args[1].upper().strip()
    
    # Verifica se o gift existe
    gift = execute_query("SELECT id, value, status, used_by FROM gift_cards WHERE code=?", (code,), True)
    
    if not gift:
        await update.message.reply_text("❌ Código inválido.")
        return
        
    gid, value, status, used_by = gift
    
    if status == 'used':
        await update.message.reply_text("⚠️ Este código já foi resgatado.")
        return
        
    # Atualiza o Gift
    execute_query("UPDATE gift_cards SET status='used', used_by=? WHERE id=?", 
                  (user_id, gid))
    
    # Adiciona saldo ao usuário
    execute_query("UPDATE users SET balance = balance + ? WHERE id=?", 
                  (value, user_id))
    
    # Registra no Ledger
    execute_query("INSERT INTO ledger(user_id, delta, reason, ref, at) VALUES(?,?,?,?,?)",
                  (user_id, value, "Resgate Gift", code, int(time.time())))
                  
    await update.message.reply_text(f"✅ Resgatado com sucesso!\nSaldo adicionado: {format_money(value)}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Cancelado.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Menu", callback_data="home")]]))

def main():
    init_db()
    
    application = Application.builder().token(TOKEN).build()
    
    # Conversação para Add CC
    conv_cc = ConversationHandler(
        entry_points=[CommandHandler("addcc", lambda u, c: (execute_query("SELECT 1"), addcc_start(u, c)))],
        states={
            1: [MessageHandler(filters.TEXT & ~filters.COMMAND, addcc_process)]
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: (u.message.reply_text("Cancelado"), ConversationHandler.END))]
    )
    
    # Conversação para Add Gift
    conv_gift = ConversationHandler(
        entry_points=[CommandHandler("add_gift", add_gift_start)],
        states={
            1: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_gift_process)]
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: (u.message.reply_text("Cancelado"), ConversationHandler.END))]
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("shop", shop))
    application.add_handler(CommandHandler("addcc", addcc_start))
    application.add_handler(CommandHandler("add_gift", add_gift_start)) # Novo comando admin
    
    application.add_handler(CallbackQueryHandler(buy_bin_callback, pattern="^buy_"))
    application.add_handler(CallbackQueryHandler(confirm_purchase, pattern="^confirm_"))
    application.add_handler(CallbackQueryHandler(cancel, pattern="^cancel|^buy_batch"))
    
    # Handler para resgate via texto direto ou menu
    application.add_handler(CommandHandler("resgatar", resgate_command))
    application.add_handler(CallbackQueryHandler(resgate_menu, pattern="^resgate_menu"))
    
    application.run_polling()

if __name__ == "__main__":
    main()
