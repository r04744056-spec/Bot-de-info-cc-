import telebot, time, requests, random, threading, os, sqlite3
from flask import Flask, request
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

TOKEN = os.environ.get("TOKEN")
COVER_KEY = os.environ.get("COVER_KEY", "sk_live_4n0Cs1egbMCER_n8BbQDYP3mtLRIm44B")
COVER_URL = os.environ.get("COVER_URL", "https://app.corvenpay.com/v1").strip().rstrip("/")
ID_DONO = 8742219720 # <-- TROCA PELO SEU ID, manda /meuid no bot pra pegar

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)
DB = "banco.db"

# ============ BANCO ============
def init_db():
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS carteira (user_id INTEGER PRIMARY KEY, saldo REAL DEFAULT 0)")
    cur.execute("CREATE TABLE IF NOT EXISTS produtos (codigo_key TEXT PRIMARY KEY, nome TEXT, preco REAL, codigo TEXT, level TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS entregas (id INTEGER PRIMARY KEY AUTOINCREMENT, codigo_key TEXT, conteudo TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS pedidos (id TEXT PRIMARY KEY, user_id INTEGER, produto TEXT, codigo TEXT, level TEXT, codigo_key TEXT, status TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS depositos (id TEXT PRIMARY KEY, user_id INTEGER, valor REAL)")
    con.commit()
    # Produtos iniciais - só cria se tiver vazio
    cur.execute("SELECT COUNT(*) FROM produtos")
    if cur.fetchone()[0] == 0:
        iniciais = [
            ("NETFLIX01", "Netflix - 1 mes", 15.00, "0829", "PADRAO"),
            ("NETFLIX02", "Netflix Premium - 1 mes", 22.00, "0830", "PREMIUM"),
            ("DISNEY01", "Disney+ Padrao - 1 mes", 12.00, "3040", "PADRAO"),
            ("DISNEY02", "Disney+ Premium - 1 mes", 18.00, "3041", "PREMIUM"),
            ("EBOOK01", "E-book Renda Extra", 9.90, "5001", "UNICO"),
        ]
        cur.executemany("INSERT INTO produtos VALUES (?,?,?,?,?)", iniciais)
        con.commit()
    con.close()
init_db()

def saldo(uid):
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("SELECT saldo FROM carteira WHERE user_id=?", (uid,))
    r = cur.fetchone(); con.close()
    return r[0] if r else 0.0

def add_saldo(uid, valor):
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("INSERT INTO carteira (user_id, saldo) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET saldo = saldo + ?", (uid, valor, valor))
    con.commit(); con.close()

def sub_saldo(uid, valor):
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("INSERT INTO carteira (user_id, saldo) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET saldo = saldo - ?", (uid, 0, valor))
    con.commit(); con.close()

def get_produtos():
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("SELECT codigo_key, nome, preco, codigo, level FROM produtos ORDER BY preco ASC")
    rows = cur.fetchall(); con.close()
    return rows

def get_produto(cod_key):
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("SELECT codigo_key, nome, preco, codigo, level FROM produtos WHERE codigo_key=?", (cod_key,))
    r = cur.fetchone(); con.close(); return r

def get_entrega(cod_key):
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("SELECT id, conteudo FROM entregas WHERE codigo_key=? ORDER BY id ASC LIMIT 1", (cod_key,))
    r = cur.fetchone()
    if r:
        cur.execute("DELETE FROM entregas WHERE id=?", (r[0],)); con.commit()
    con.close(); return r[1] if r else None

def count_entrega(cod_key):
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM entregas WHERE codigo_key=?", (cod_key,))
    r = cur.fetchone()[0]; con.close(); return r

def add_entrega_db(cod_key, conteudo):
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("INSERT INTO entregas (codigo_key, conteudo) VALUES (?, ?)", (cod_key, conteudo))
    con.commit(); con.close()

# ============ PIX ============
def criar_pix(ref, valor, desc):
    headers = {"Authorization": "Bearer " + COVER_KEY, "Content-Type": "application/json"}
    webhook = os.environ.get("WEBHOOK_URL", "https://SEU-LINK-RAILWAY/webhook")
    dados = {"amount": int(valor*100), "paymentMethod": "pix", "description": desc, "external_id": ref, "webhook_url": webhook}
    try:
        r = requests.post(COVER_URL + "/transactions", json=dados, headers=headers, timeout=15)
        if r.status_code in [200, 201]:
            j = r.json()
            pix = j.get("pix") or j.get("data") or j
            return pix.get("copy_paste") or pix.get("pix_copy_paste") or pix.get("qr_code") or ""
    except Exception as e:
        print("Erro PIX:", e)
    return None

def entregar(pid):
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("SELECT user_id, produto, codigo, level, codigo_key, status FROM pedidos WHERE id=?", (pid,))
    ped = cur.fetchone(); con.close()
    if not ped or ped[5] == "ENTREGUE": return
    entrega = get_entrega(ped[4])
    if not entrega:
        bot.send_message(ped[0], "Estoque acabou! Fala com o dono.")
        bot.send_message(ID_DONO, f"🚨 ESTOQUE ZERADO {ped[4]} - Pedido {pid}")
        return
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("UPDATE pedidos SET status='ENTREGUE' WHERE id=?", (pid,))
    con.commit(); con.close()
    bot.send_message(ped[0], f"✅ COMPRA APROVADA!\n\nPedido #{pid}\n{ped[1]}\nCodigo: {ped[2]} | Level: {ped[3]}\n\n🎁 ENTREGA:\n{entrega}\n\nSaldo: R$ {saldo(ped[0]):.2f}")

def comprar_pelo_botao(uid, cod_key):
    pr = get_produto(cod_key)
    if not pr:
        bot.send_message(uid, "❌ Produto não existe mais"); return
    # pr = (codigo_key, nome, preco, codigo, level)
    if count_entrega(cod_key) == 0:
        bot.send_message(uid, "❌ Esgotado!"); return
    if saldo(uid) < pr[2]:
        falta = pr[2] - saldo(uid)
        bot.send_message(uid, f"❌ Falta R$ {falta:.2f} pra {pr[1]}\nRecarrega: /depositar {int(falta+5)}")
        return
    sub_saldo(uid, pr[2])
    pid = str(random.randint(1000, 9999))
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("INSERT INTO pedidos VALUES (?, ?, ?, ?, ?, ?, ?)", (pid, uid, pr[1], pr[3], pr[4], cod_key, "PAGO"))
    con.commit(); con.close()
    entregar(pid)

def teclado_loja():
    mk = InlineKeyboardMarkup()
    produtos = get_produtos()
    # 2 por linha
    for i in range(0, len(produtos), 2):
        linha = []
        for pr in produtos[i:i+2]:
            # pr: codigo_key, nome, preco, codigo, level
            estoque = count_entrega(pr[0])
            txt = f"{pr[1]} - R$ {pr[2]:.2f} ({estoque})"
            linha.append(InlineKeyboardButton(txt, callback_data=f"comprar_{pr[0]}"))
        mk.add(*linha)
    mk.add(InlineKeyboardButton("⬅️ Voltar", callback_data="voltar_inicio"))
    return mk

# ============ WEBHOOK ============
@app.route('/webhook', methods=['POST'])
def webhook():
    dados = request.json or {}
    status = str(dados.get("status") or dados.get("data", {}).get("status") or "").lower()
    ref = str(dados.get("external_id") or dados.get("data", {}).get("external_id") or "")
    if status in ["paid", "approved", "pago", "aprovado", "confirmed"]:
        con = sqlite3.connect(DB); cur = con.cursor()
        cur.execute("SELECT user_id, valor FROM depositos WHERE id=?", (ref,))
        dep = cur.fetchone()
        if dep:
                        cur.execute("DELETE FROM depositos WHERE id=?", (ref,))

{"Approved": "False", "Response": "Not authorized", "Gate": "Assas Auth", "dev": "https://t.me/npnbit4", "Price": "0.00", "Time": "2.01s", "CC": "4381085031682075|01|31|471", "Raw": "{\"error\":\"N\u00e3o autorizado\"}", "Proxy": "direct", "Profile": "quero"}

@app.route('/', methods=['GET'])
def home():
    return "Bot online!", 200

# ============ CLIENTE ============
@bot.message_handler(commands=['start'])
def start(m):
    uid = m.from_user.id
    texto = f"💲 Carteira:\n├ ID: {uid}\n├ 💸 Saldo: R$ {saldo(uid):.2f}\n└ 🌟 Pontos: 0.00 (~R$ 0.00)"
    mk = InlineKeyboardMarkup()
    mk.add(InlineKeyboardButton("🛒 Comprar", callback_data="menu_loja"))
    mk.add(InlineKeyboardButton("👤 Minha conta", callback_data="menu_conta"), InlineKeyboardButton("💠 PIX Rápido", callback_data="menu_pix"))
    mk.add(InlineKeyboardButton("📋 Regras", callback_data="menu_regras"), InlineKeyboardButton("🔄 Trocas", callback_data="menu_trocas"))
    with open("IMG_1430.jpeg", "rb") as foto:
        bot.send_photo(m.chat.id, foto, caption=texto, reply_markup=mk)

@bot.callback_query_handler(func=lambda call: call.data.startswith("menu_"))
def menu_callback(call):
    uid = call.from_user.id
    acao = call.data
    if acao == "menu_loja":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, f"🛍️ LOJA - Saldo: R$ {saldo(uid):.2f}\nEscolha um produto:", reply_markup=teclado_loja())
    elif acao == "menu_conta":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, f"👤 Minha conta\n\nID: {uid}\nSaldo: R$ {saldo(uid):.2f}\nPontos: 0,00")
    elif acao == "menu_pix":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "💠 PIX Rápido\n\nManda assim:\n/depositar 20")
    elif acao == "menu_regras":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "📋 Regras\n\n1. Sem reembolso após entrega\n2. Não compartilhe o acesso\n3. Troca só se o acesso não funcionar em 24h")
    elif acao == "menu_trocas":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "🔄 Trocas\n\nSe seu acesso não funcionou, manda print aqui e fala com o dono.")

@bot.callback_query_handler(func=lambda call: call.data == "voltar_inicio")
def voltar_inicio(call):
    bot.answer_callback_query(call.id)
    m_fake = call.message
    m_fake.from_user = call.from_user
    start(m_fake)

@bot.message_handler(commands=['depositar'])
def depositar(m):
    p = m.text.split()
    if len(p) < 2:
        bot.send_message(m.chat.id, "Usa: /depositar 20"); return
    try: valor = float(p[1].replace(",", "."))
    except: bot.send_message(m.chat.id, "Ex: /depositar 20"); return
    if valor < 5 or valor > 300:
        bot.send_message(m.chat.id, "❌ De R$5 a R$300"); return
    dep_id = "DEP" + str(random.randint(1000, 9999))
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("INSERT INTO depositos VALUES (?, ?, ?)", (dep_id, m.from_user.id, valor))
    con.commit(); con.close()
    copia = criar_pix(dep_id, valor, f"Deposito {dep_id}")
    if not copia:
        bot.send_message(m.chat.id, "Erro ao criar PIX"); return
    bot.send_message(m.chat.id, f"💰 DEPOSITO {dep_id}\nR$ {valor:.2f}\n\nPIX:\n{copia}")

@bot.message_handler(commands=['loja'])
def loja(m):
    bot.send_message(m.chat.id, f"🛍️ LOJA - Saldo: R$ {saldo(m.from_user.id):.2f}\nEscolha um produto:", reply_markup=teclado_loja())

@bot.callback_query_handler(func=lambda call: call.data.startswith("comprar_"))
def callback_comprar(call):
    cod_key = call.data.replace("comprar_", "")
    bot.answer_callback_query(call.id, "Processando...")
    comprar_pelo_botao(call.from_user.id, cod_key)

# ============ PAINEL DO DONO - TUDO PELO TELEGRAM ============
def so_dono(m):
    return m.from_user.id == ID_DONO

@bot.message_handler(commands=['meuid'])
def meuid(m):
    bot.send_message(m.chat.id, f"Seu ID: {m.from_user.id}")

@bot.message_handler(commands=['painel'])
def painel(m):
    if not so_dono(m): return
    bot.send_message(m.chat.id, """👑 PAINEL DO DONO

🛒 PRODUTOS:
/addproduto CHAVE Nome Preco Codigo Level
Ex: /addproduto HBO01 HBO Max 1 mes 18.00 0999 PREMIUM
/delproduto CHAVE
Ex: /delproduto HBO01
/listaprodutos
/editarpreco CHAVE NOVOPRECO
Ex: /editarpreco NETFLIX01 17.00

📦 ESTOQUE:
/addentrega CHAVE Login: xxx | Senha: yyy
/estoque
/limparestoque CHAVE

💰 GRANA:
/addsaldo ID VALOR
/saldo ID
""")

@bot.message_handler(commands=['addproduto'])
def addproduto(m):
    if not so_dono(m): return
    # /addproduto HBO01 HBO Max 1 mes 18.00 0999 PREMIUM
    partes = m.text.split()
    if len(partes) < 6:
        bot.send_message(m.chat.id, "Usa assim:\n/addproduto HBO01 HBO Max 1 mes 18.00 0999 PREMIUM\n\nCHAVE = sem espaço\nNome = pode ter espaço\nPreco = 18.00\nCodigo = 0999\nLevel = PREMIUM ou PADRAO"); return
    try:
        chave = partes[1].upper()
        preco = float(partes[-3].replace(",", "."))
        codigo = partes[-2]
        level = partes[-1].upper()
        nome = " ".join(partes[2:-3])
        con = sqlite3.connect(DB); cur = con.cursor()
        cur.execute("INSERT OR REPLACE INTO produtos VALUES (?, ?, ?, ?, ?)", (chave, nome, preco, codigo, level))
        con.commit(); con.close()
        bot.send_message(m.chat.id, f"✅ Produto criado!\n\n{chave} - {nome}\nR$ {preco:.2f} | Codigo {codigo} | {level}\n\nAgora adiciona estoque:\n/addentrega {chave} Login: xxx | Senha: yyy")
    except Exception as e:
        bot.send_message(m.chat.id, f"Erro: {e}\nEx: /addproduto HBO01 HBO Max 1 mes 18.00 0999 PREMIUM")

@bot.message_handler(commands=['delproduto'])
def delproduto(m):
    if not so_dono(m): return
    p = m.text.split()
    if len(p) < 2: return
    chave = p[1].upper()
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("DELETE FROM produtos WHERE codigo_key=?", (chave,))
    cur.execute("DELETE FROM entregas WHERE codigo_key=?", (chave,))
    con.commit(); con.close()
    bot.send_message(m.chat.id, f"✅ Produto {chave} apagado!")

@bot.message_handler(commands=['listaprodutos'])
def listaprodutos(m):
    if not so_dono(m): return
    prods = get_produtos()
    txt = "🛒 SEUS PRODUTOS:\n\n"
    for pr in prods:
        txt += f"{pr[0]} - {pr[1]}\nR$ {pr[2]:.2f} | Cod {pr[3]} | {pr[4]} | Estoque: {count_entrega(pr[0])}\n\n"
    bot.send_message(m.chat.id, txt)

@bot.message_handler(commands=['editarpreco'])
def editarpreco(m):
    if not so_dono(m): return
    p = m.text.split()
    if len(p) < 3:
        bot.send_message(m.chat.id, "Usa: /editarpreco NETFLIX01 17.00"); return
    chave = p[1].upper()
    try: novo = float(p[2].replace(",", "."))
    except: bot.send_message(m.chat.id, "Preço inválido"); return
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("UPDATE produtos SET preco=? WHERE codigo_key=?", (novo, chave))
    con.commit(); con.close()
    bot.send_message(m.chat.id, f"✅ Preço de {chave} alterado pra R$ {novo:.2f}")

@bot.message_handler(commands=['addentrega'])
def addentrega(m):
    if not so_dono(m): return
    partes = m.text.split(maxsplit=2)
    if len(partes) < 3:
        bot.send_message(m.chat.id, "Usa: /addentrega NETFLIX01 Login: xxx | Senha: yyy"); return
    cod_key = partes[1].upper()
    add_entrega_db(cod_key, partes[2])
    bot.send_message(m.chat.id, f"✅ Adicionado em {cod_key}! Agora tem {count_entrega(cod_key)} em estoque")

@bot.message_handler(commands=['estoque'])
def estoque(m):
    if not so_dono(m): return
    prods = get_produtos()
    txt = "📦 ESTOQUE:\n\n"
    for pr in prods:
        txt += f"{pr[0]} - {pr[1]}: {count_entrega(pr[0])} unidades\n"
    bot.send_message(m.chat.id, txt)

@bot.message_handler(commands=['limparestoque'])
def limparestoque(m):
    if not so_dono(m): return
    p = m.text.split()
    if len(p) < 2: return
    chave = p[1].upper()
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("DELETE FROM entregas WHERE codigo_key=?", (chave,))
    con.commit(); con.close()
    bot.send_message(m.chat.id, f"✅ Estoque de {chave} limpo!")

@bot.message_handler(commands=['addsaldo'])
def addsaldo(m):
    if not so_dono(m): return
    p = m.text.split()
    if len(p) < 3: return
    add_saldo(int(p[1]), float(p[2]))
    bot.send_message(m.chat.id, f"✅ Adicionado R$ {p[2]} pra {p[1]}")
    try: bot.send_message(int(p[1]), f"💰 Você recebeu R$ {p[2]} de saldo! Saldo atual: R$ {saldo(int(p[1])):.2f}")
    except: pass

@bot.message_handler(commands=['saldo'])
def ver_saldo(m):
    if not so_dono(m): return
    p = m.text.split()
    if len(p) < 2: return
    uid = int(p[1])
    bot.send_message(m.chat.id, f"Saldo de {uid}: R$ {saldo(uid):.2f}")

def roda_bot():
    bot.delete_webhook(drop_pending_updates=True)
    time.sleep(1)
    bot.infinity_polling()

if __name__ == "__main__":
    threading.Thread(target=roda_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
