import telebot
from telebot import types
import sqlite3
import requests
import time
import secrets
import string
import random
import re
import logging
from datetime import datetime
import threading

# --- CONFIGURAÇÕES DO BOT ---
API_TOKEN = 'GLADE_API'  # Substitua pelo seu token
STRIPE_SK = ''                # Mantenha vazio ou remova se não usar mais
GLADE_KEY = 'GLADE_URL'
GLADE_MID = 'SEU_MERCHANT_ID_AQUI'

# Inicializa o Bot
bot = telebot.TeleBot(BOT_TOKEN)

# --- BANCO DE DADOS (SQLite) ---
conn = sqlite3.connect('bot_database.db', check_same_thread=False)
cursor = conn.cursor()

def init_db():
    """Cria as tabelas necessárias"""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 0.00,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gifts (
            code TEXT PRIMARY KEY,
            value REAL,
            used_by INTEGER,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()

init_db()

# --- FUNÇÕES DA CARTEIRA ---

def get_user(user_id):
    """Retorna ou cria um usuário no banco"""
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    if not user:
        cursor.execute("INSERT INTO users (user_id, balance) VALUES (?, ?)", (user_id, 0.00))
        conn.commit()
        return {'id': user_id, 'balance': 0.00}
    return {'id': user_id, 'balance': user[2]}

def update_balance(user_id, amount):
    """Atualiza o saldo do usuário (+ ou -)"""
    current = get_user(user_id)['balance']
    new_balance = current + amount
    
    cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
    
    # Registra transação
    type_op = 'deposit' if amount > 0 else 'purchase'
    cursor.execute("""
        INSERT INTO transactions (user_id, type, amount, status) 
        VALUES (?, ?, ?, 'completed')
    """, (user_id, type_op, abs(amount)))
    
    conn.commit()
    return new_balance

def generate_gift_code(length=12):
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

# --- LÓGICA GLADEPAY (PRESERVADA DO SEU SCRIPT) ---

def glade_headers():
    return {"key": GLADE_KEY, "mid": GLADE_MID, "Content-Type": "application/json"}

def glade_create(order_id, amount, user):
    ref = f"ORD{order_id}-{secrets.token_hex(4)}"
    payload = {
        "amount": int(amount * 100), # Glade geralmente pede centavos
        "currency": "BRL",
        "reference": ref,
        "customer": {
            "email": f"user_{user}@bot.com",
            "name": "User Bot"
        },
        "payment_method": "credit_card",
        "card": {
            "number": "", # Será preenchido dinamicamente
            "expiry_month": "",
            "expiry_year": "",
            "cvv": ""
        }
    }
    return payload, ref

def do_glade_check(card_data):
    """
    Função simplificada para testar no Glade.
    Note: A implementação real do Glade pode variar dependendo da API específica.
    Aqui simulo a chamada baseada na estrutura que você forneceu.
    """
    try:
        # Simulando a chamada Glade
        # Se você tiver a URL exata, substitua aqui
        
        # Exemplo genérico de payload Glade:
        payload_data, ref = glade_create(12345, 0.00, "test")
        
        # Aqui você faria o POST real para a API do Glade
        # headers = glade_headers()
        # response = requests.post("URL_DA_API_GLADE", json=payload_data, headers=headers)
        
        # Para fins deste exemplo, vou simular uma resposta baseada em regras comuns
        # Você deve substituir isso pela chamada real `requests.post(...)`
        
        # Simulação simples para demonstração:
        # Se o número terminar em par, aprova, senão recusa (exemplo)
        last_digit = int(card_data['num'][-1])
        if last_digit % 2 == 0:
            return "APROVADO (GLADE)"
        else:
            return "RECUSADO (GLADE)"

    except Exception as e:
        return f"ERRO GLADE: {str(e)}"

# --- COMANDOS DO BOT ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn_wallet = types.KeyboardButton("💳 Carteira")
    btn_pix = types.KeyboardButton("💎 Depositar (PIX)")
    btn_gift = types.KeyboardMessageOptions("🎁 Gerar Gift")
    btn_buy = types.KeyboardButton("🛒 Comprar Chk")
    
    markup.row(btn_wallet, btn_pix)
    markup.row(btn_gift, btn_buy)
    
    bot.reply_to(message, 
                 f"Olá! Bem-vindo ao Bot.\n\n"
                 f"Seu Saldo Atual: R$ {get_user(message.from_user.id)['balance']:.2f}\n\n"
                 f"Escolha uma opção:", reply_markup=markup)

@bot.message_handler(content_types=['text'])
def handle_text(message):
    chat_id = message.from_user.id
    text = message.text.strip()
    user = get_user(chat_id)
    
    if text == "💳 Carteira":
        bal = user['balance']
        msg = f"💰 Sua Carteira:\n\n"
        msg += f"Saldo: R$ {bal:.2f}\n"
        msg += f"Status: Ativo\n\n"
        msg += "Use /pix para depositar ou /chk para testar."
        bot.reply_to(message, msg)
        
    elif text == "💎 Depositar (PIX)":
        # Instruções para depósito
        msg = "📲 **Depósito via PIX**\n\n"
        msg += "Chave PIX: `minha-chave-aqui@email.com`\n"
        msg += "Nome: Seu Nome\n\n"
        msg += "Após pagar, digite /confirmar [valor] para liberar seu saldo.\n"
        msg += "(Ex: /confirmar 10.00)\n\n"
        msg += "⚠️ Taxa: Grátis"
        bot.send_message(chat_id, msg, parse_mode="Markdown")
        
    elif text.startswith("/confirmar"):
        parts = text.split()
        if len(parts) >= 2:
            try:
                val = float(parts[1])
                # Aqui você poderia verificar o DB de pagamentos reais
                # Por enquanto, apenas adicionamos o saldo
                new_bal = update_balance(chat_id, val)
                bot.reply_to(message, f"✅ Depósito de R$ {val:.2f} confirmado!\nNovo Saldo: R$ {new_bal:.2f}")
            except ValueError:
                bot.reply_to(message, "Valor inválido. Use: /confirmar 10.00")
                
    elif text.startswith("/gerargift"):
        parts = text.split()
        if len(parts) >= 2:
            try:
                val = float(parts[1])
                code = generate_gift_code()
                
                # Salva no banco
                cursor.execute("INSERT INTO gifts (code, value) VALUES (?, ?)", (code, val))
                conn.commit()
                
                bot.reply_to(message, f"🎁 **Gift Gerado!**\n\nCódigo: `{code}`\nValor: R$ {val:.2f}\n\nEnvie este código para seus amigos!")
                
            except ValueError:
                bot.reply_to(message, "Uso: /gerargift valor")
                
    elif text.startswith("/resgatar"):
        parts = text.split()
        if len(parts) >= 2:
            code = parts[1]
            cursor.execute("SELECT * FROM gifts WHERE code = ?", (code,))
            gift = cursor.fetchone()
            
            if gift:
                if gift[3] == 'used':
                    bot.reply_to(message, "❌ Este gift já foi resgatado.")
                else:
                    # Resgata
                    cursor.execute("UPDATE gifts SET used_by = ?, status = 'used' WHERE code = ?", (chat_id, code))
                    conn.commit()
                    
                    new_bal = update_balance(chat_id, gift[1])
                    bot.reply_to(message, f"✅ Gift Resgatado!\n+ R$ {gift[1]:.2f}\nNovo Saldo: R$ {new_bal:.2f}")
            else:
                bot.reply_to(message, "❌ Código de gift inválido.")

    elif text.startswith("/chk"):
        # Novo Checker usando Glade
        card_str = text.replace("/chk ", "").strip()
        if not card_str:
            bot.reply_to(message, "Uso: /chk 4111111111111111|12|25|123")
            return
            
        # Verifica saldo mínimo para chk (ex: 0.50)
        cost = 0.50
        if user['balance'] < cost:
            bot.reply_to(message, f"❌ Saldo insuficiente. Custo do chk: R$ {cost:.2f}")
            return
            
        # Deduz saldo
        update_balance(chat_id, -cost)
        
        # Formata cartão
        parts = card_str.split('|')
        if len(parts) != 4:
            bot.reply_to(message, "Formato errado. Use: num|mm|yy|cvv")
            # Reverte saldo? Melhor não, ou avisa. Vamos assumir que o user errou e perde o custo.
            return
            
        num, mm, yy, cvv = parts
        
        # Executa Check Glade
        bot.send_chat_action(chat_id, 'typing')
        result_msg = bot.send_message(chat_id, "🔄 Testando no Glade...")
        
        # Chamada real (simulada aqui, ajuste para sua lógica exata)
        # Como não tenho a URL exata do endpoint de CHECK do Glade, uso a lógica de headers que você tinha.
        # Você precisa ajustar esta parte para fazer o POST real na API do Glade.
        
        # Exemplo de chamada real (ajuste conforme documentação Glade):
        try:
            # Headers corretos
            headers = glade_headers()
            
            # Payload típico Glade para teste (verifique a documentação Glade para o endpoint exato)
            # Geralmente é algo como /charges ou /payments
            payload = {
                "amount": 100, # 1.00 BRL
                "currency": "BRL",
                "reference": f"CHK-{int(time.time())}",
                "card": {
                    "number": num,
                    "exp_month": mm,
                    "exp_year": yy,
                    "cvv": cvv
                }
            }
            
            # Substitua 'URL_CHECK_GLADE' pela URL real da API Glade
            # response = requests.post("https://api.gladepay.com/v1/charges", json=payload, headers=headers)
            
            # SIMULAÇÃO PARA TESTE (Remova quando colocar a chamada real)
            time.sleep(1) # Simula delay
            # Lógica simples de simulação:
            if num.startswith("4"):
                status = "APROVADO (GLADE)"
            else:
                status = "RECUSADO (GLADE)"
            # Fim Simulação
            
            # Se fosse real seria:
            # j = response.json()
            # if j.get('status') == 'success': status = "APROVADO"
            # else: status = "RECUSADO"
            
            final_msg = f"💳 **Resultado Glade**\n\n"
            final_msg += f"Cartão: {num[:6]}****{num[-4:]}\n"
            final_msg += f"Status: {status}\n"
            final_msg += f"Saldo Restante: R$ {get_user(chat_id)['balance']:.2f}"
            
            bot.edit_message_text(final_msg, chat_id=result_msg.chat_id, message_id=result_msg.message_id, parse_mode="Markdown")
            
        except Exception as e:
            bot.edit_message_text(f"❌ Erro ao conectar com Glade: {str(e)}", chat_id=result_msg.chat_id, message_id=result_msg.message_id)

    elif text == "🛒 Comprar Chk":
        bal = user['balance']
        price = 0.50
        msg = f"💳 **Pacote de Checks Glade**\n\n"
        msg += f"Preço: R$ {price:.2f} por teste\n"
        msg += f"Seu Saldo: R$ {bal:.2f}\n\n"
        msg += "Você também pode usar o comando direto:\n`/chk 4111...|12|25|123`"
        bot.reply_to(message, msg, parse_mode="Markdown")

# Inicia o polling
print("Bot starting...")
bot.infinity_polling()
