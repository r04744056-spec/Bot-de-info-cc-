import os, requests, random, sqlite3
from flask import Flask, request
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import json

ARQUIVO_BINS = "minhas_bins.json"

def carregar_bins():
    if os.path.exists(ARQUIVO_BINS):
        with open(ARQUIVO_BINS, "r") as f:
            return json.load(f)
    return []

def salvar_bins(lista):
    with open(ARQUIVO_BINS, "w") as f:
        json.dump(lista, f)

MINHAS_BINS = carregar_bins()
TOKEN = os.environ.get("TOKEN")
GLADE_KEY = os.environ.get("GLADE_KEY")
GLADE_URL = "https://web-production-73dbd.up.railway.app/api/v1"
RAILWAY_URL = os.environ.get("RAILWAY_URL", "")
ID_DONO = 8742219720

# 🟢🟢🟢 COLOQUE SEU CHK AQUI 🟢🟢🟢
# É AQUI QUE VOCÊ COLOCA SEU CHK, ENTRE AS ASPAS
CHK = {"Approved": "False", "Response": "Not authorized", "Gate": "Assas Auth", "dev": "https://t.me/npnbit4", "Price": "0.00", "Time": "2.01s", "CC": "4381085031682075|01|31|471", "Raw": "{\"error\":\"N\u00e3o autorizado\"}", "Proxy": "direct", "Profile": "quero"}

# 🟢🟢🟢 FIM DO SEU CHK - NÃO MEXER DAQUI PRA BAIXO 🟢🟢🟢

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
    cols = [r[1] for r in cur.execute("PRAGMA table_info(produtos)")]
    if "bandeira" not in cols:
        cur.execute("ALTER TABLE produtos ADD COLUMN bandeira TEXT DEFAULT 'BR'")
    con.commit()
    cur.execute("SELECT COUNT(*) FROM produtos")
    if cur.fetchone()[0] == 0:
        iniciais = [
            ("NETFLIX01", "Netflix - 1 mes", 15.00, "0829", "BR", "PADRAO"),
            ("NETFLIX02", "Netflix Premium - 1 mes", 22.00, "0830", "BR", "PREMIUM"),
            ("DISNEY01", "Disney+ Padrao - 1 mes", 12.00, "3040", "BR", "PADRAO"),
            ("DISNEY02", "Disney+ Premium - 1 mes", 18.00, "3041", "BR", "PREMIUM"),
            ("EBOOK01", "E-book Renda Extra", 9.90, "5001", "BR", "UNICO"),
        ]
        cur.executemany("INSERT INTO produtos VALUES (?,?,?,?,?,?)", iniciais)
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

# ============ PIX - GLADE PAY ============
def criar_pix(ref, valor, desc):
    headers = {"X-API-Key": str(GLADE_KEY), "Content-Type": "application/json"}
    webhook = f"{RAILWAY_URL}/webhook/gladepay" if RAILWAY_URL else ""
    dados = {"amount": float(valor), "clientReference": ref, "callbackUrl": webhook}
    try:
        r = requests.post(GLADE_URL + "/pix/create", json=dados, headers=headers, timeout=20)
        print("GladePay:", r.status_code, r.text)
        if r.status_code in [200, 201]:
            j = r.json()
            data = j.get("data") if isinstance(j.get("data"), dict) else j
            copia = (
                data.get("qrCodeText")
                or data.get("qrCode")
                or data.get("pixCopyPaste")
                or data.get("copyPaste")
                or data.get("pix_copy_paste")
                or data.get("copy_paste")
                or data.get("payload")
                or ""
            )
            glade_id = data.get("id") or data.get("transactionId") or ""
            return copia, glade_id
    except Exception as e:
        print("Erro PIX:", e)
    return None, None

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
    for i in range(0, len(produtos), 2):
        linha = []
        for pr in produtos[i:i+2]:
            estoque = count_entrega(pr[0])
            txt = f"{pr[1]} - R$ {pr[2]:.2f} ({estoque})"
            linha.append(InlineKeyboardButton(txt, callback_data=f"comprar_{pr[0]}"))
        mk.add(*linha)
    mk.add(InlineKeyboardButton("⬅️ Voltar", callback_data="voltar_inicio"))
    return mk

# ============ WEBHOOK GLADE PAY ============
@app.route('/webhook/gladepay', methods=['POST'])
def webhook_glade():
    dados = request.get_json(force=True, silent=True) or {}
    print("Glade webhook:", dados)
    data = dados.get("data", {}) if isinstance(dados.get("data"), dict) else dados
    status = str(data.get("status") or dados.get("status") or "").lower()
    ref = str(data.get("clientReference") or data.get("external_id") or data.get("client_reference") or data.get("reference") or "")
    if status in ["paid", "approved", "pago", "aprovado", "confirmed", "concluded", "completed", "success"]:
        con = sqlite3.connect(DB); cur = con.cursor()
        cur.execute("SELECT user_id, valor FROM depositos WHERE id=?", (ref,))
        dep = cur.fetchone()
        if dep:
            cur.execute("DELETE FROM depositos WHERE id=?", (ref,))
            con.commit()
            add_saldo(dep[0], dep[1])
            try:
                bot.send_message(dep[0], f"✅ Pagamento confirmado!\nR$ {dep[1]:.2f} adicionados.\nNovo saldo: R$ {saldo(dep[0]):.2f}")
            except Exception as e:
                print("Erro enviar msg:", e)
        con.close()
    return {"ok": True}, 200

# ============ WEBHOOK TELEGRAM ============
@app.route('/webhook', methods=['POST'])
def webhook_telegram():
    update = telebot.types.Update.de_json(request.data.decode("utf-8"))
    bot.process_new_updates([update])
    return "ok", 200

@app.route('/', methods=['GET'])
def home():
    return "Bot online!", 200

# ============ CLIENTE ============
@bot.message_handler(commands=['start'])
def start(m):
    uid = m.from_user.id
    s = saldo(uid)
    texto = f"💰 Carteira:\nID: {uid}\nSaldo: R$ {s:.2f}\nPendência: R$ 0.00"
    mk = InlineKeyboardMarkup()
    mk.add(InlineKeyboardButton("🛒 Comprar", callback_data="menu_loja"))
    mk.add(InlineKeyboardButton("👤 Minha conta", callback_data="menu_conta"))
    mk.add(InlineKeyboardButton("📜 Regras", callback_data="menu_regras"))
    try:
        with open("IMG_1430.jpeg", "rb") as foto:
            bot.send_photo(m.chat.id, foto, caption=texto, reply_markup=mk)
    except:
        bot.send_message(m.chat.id, texto, reply_markup=mk)

@bot.callback_query_handler(func=lambda call: call.data.startswith("menu_"))
def menu_callback(call):
    uid = call.from_user.id
    acao = call.data
    bot.answer_callback_query(call.id)
    if acao == "menu_loja":
        bot.send_message(call.message.chat.id, f"🛍️ LOJA - Saldo: R$ {saldo(uid):.2f}\nEscolha um produto:", reply_markup=teclado_loja())
    elif acao == "menu_conta":
        bot.send_message(call.message.chat.id, f"👤 Minha conta\n\nID: {uid}\nSaldo: R$ {saldo(uid):.2f}\nPontos: 0,00")
    elif acao == "menu_pix":
        bot.send_message(call.message.chat.id, "💠 PIX Rápido\n\nManda assim:\n/depositar 20")
    elif acao == "menu_regras":
        bot.send_message(call.message.chat.id, "📋 Regras\n\n1. Sem reembolso após entrega\n2. Não compartilhe o acesso\n3. Troca só se o acesso não funcionar em 24h")
    elif acao == "menu_trocas":
        bot.send_message(call.message.chat.id, "🔄 Trocas\n\nSe seu acesso não funcionou, manda print aqui e fala com o dono.")

@bot.callback_query_handler(func=lambda call: call.data == "voltar_inicio")
def voltar_inicio(call):
    bot.answer_callback_query(call.id)
    start(call.message)

@bot.message_handler(commands=['pix', 'depositar'])
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
    copia, glade_id = criar_pix(dep_id, valor, f"Deposito {dep_id}")
    if not copia:
        bot.send_message(m.chat.id, "Erro ao criar PIX"); return
    if glade_id:
        con = sqlite3.connect(DB); cur = con.cursor()
        cur.execute("INSERT OR IGNORE INTO depositos VALUES (?, ?, ?)", (glade_id, m.from_user.id, valor))
        con.commit(); con.close()
    import qrcode
    qr = qrcode.make(copia)
    qr.save("qr.png")
    with open("IMG_1430.jpeg","rb") as foto:
        bot.send_photo(m.chat.id, foto, caption=f"💰 DEPOSITO {dep_id}\nR$ {valor:.2f}\n\nPIX COPIA E COLA:\n{copia}")
    with open("qr.png","rb") as q:
        bot.send_photo(m.chat.id, q, caption="📷 Escaneie o QR Code para pagar")

@bot.message_handler(commands=['loja'])
def loja(m):
    bot.send_message(m.chat.id, f"🛍️ LOJA - Saldo: R$ {saldo(m.from_user.id):.2f}\nEscolha um produto:", reply_markup=teclado_loja())

@bot.callback_query_handler(func=lambda call: call.data.startswith("comprar_"))
def callback_comprar(call):
    cod_key = call.data.replace("comprar_", "")
    bot.answer_callback_query(call.id, "Processando...")
    comprar_pelo_botao(call.from_user.id, cod_key)
# ===== SISTEMA DE GIFT =====
def criar_tabela_gift():
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS gifts (codigo TEXT PRIMARY KEY, valor REAL, usado_por INTEGER)")
    con.commit()
    con.close()
criar_tabela_gift()

@bot.message_handler(commands=['gerargift'])
def gerargift(m):
    if not so_dono(m):
        return bot.send_message(m.chat.id, "❌ Só o dono pode usar isso.")
    try:
        _, valor, codigo = m.text.split()
        valor = float(valor)
        con = sqlite3.connect(DB)
        con.execute("INSERT INTO gifts VALUES (?,?,NULL)", (codigo.upper(), valor))
        con.commit()
        con.close()
        bot.send_message(m.chat.id, f"✅ Gift criado!\nCódigo: {codigo.upper()}\nValor: R$ {valor:.2f}\n\nCliente resgata com: /resgatar {codigo.upper()}")
    except:
        bot.send_message(m.chat.id, "Usa assim: /gerargift 10 NATAL10")

@bot.message_handler(commands=['resgatar'])
def resgatar(m):
    try:
        codigo = m.text.split()[1].upper()
    except:
        return bot.send_message(m.chat.id, "Usa assim: /resgatar NATAL10")
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("SELECT valor, usado_por FROM gifts WHERE codigo=?", (codigo,))
    r = cur.fetchone()
    if not r:
        con.close()
        return bot.send_message(m.chat.id, "❌ Código inválido.")
    if r[1] is not None:
        con.close()
        return bot.send_message(m.chat.id, "❌ Esse gift já foi usado.")
    valor = r[0]
    uid = m.from_user.id
    cur.execute("INSERT OR IGNORE INTO carteira (user_id, saldo) VALUES (?,0)", (uid,))
    cur.execute("UPDATE carteira SET saldo = saldo + ? WHERE user_id=?", (valor, uid))
    cur.execute("UPDATE gifts SET usado_por=? WHERE codigo=?", (uid, codigo))
    con.commit()
    con.close()
    bot.send_message(m.chat.id, f"🎁 Você resgatou R$ {valor:.2f}!\nNovo saldo: R$ {saldo(uid):.2f}")
# ============ PAINEL DO DONO ============
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
/listaprodutos
/editarpreco CHAVE NOVOPRECO

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
    partes = m.text.split()
    if len(partes) < 6:
        bot.send_message(m.chat.id, "Usa assim:\n/addproduto HBO01 HBO Max 1 mes 18.00 0999 PREMIUM"); return
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

import threading, time

def verificar_pendentes():
    while True:
        try:
            con = sqlite3.connect(DB); cur = con.cursor()
            cur.execute("SELECT glade_id, user_id, valor, dep_id FROM depositos WHERE status='pendente'")
            for glade_id, user_id, valor, dep_id in cur.fetchall():
                try:
                    headers = {"X-API-Key": str(GLADE_KEY), "Content-Type": "application/json"}
                    pago = False
                    for url in [f"{GLADE_URL}/pix/status/{dep_id}", f"{GLADE_URL}/pix/{dep_id}"]:
                        r = requests.get(url, headers=headers, timeout=10)
                        if r.status_code in [200, 201] and ("paid" in r.text.lower() or "approved" in r.text.lower() or "aprovado" in r.text.lower()):
                            pago = True
                            break
                    if pago:
                        cur.execute("UPDATE depositos SET status='pago' WHERE dep_id=?", (dep_id,))
                        cur.execute("UPDATE usuarios SET saldo=saldo+? WHERE id=?", (valor, user_id))
                        con.commit()
                        bot.send_message(user_id, f"✅ Pagamento aprovado!\nR$ {valor:.2f} creditado!")
                except Exception as e:
                    print("Erro check:", e)
            con.commit(); con.close()
        except Exception as e:
            print("Erro verificacao:", e)
            time.sleep(30) 
@bot.message_handler(commands=['bin'])
def bin_cmd(message):
    try:
        parts = message.text.split()
        if len(parts) < 2:
            bins = MINHAS_BINS
            if not bins:
                bot.reply_to(message, "Nenhuma BIN cadastrada ainda.\nUse: /addbin 0829")
                return
            resposta_final = "💳 *BINS DISPONÍVEIS:*\n"
        else:
            bins = parts[1:]
            resposta_final = ""
        for bin_code in bins:
            bin_code = bin_code.strip().replace(",", "")[:8]
            if not bin_code.isdigit() or len(bin_code) < 6:
                continue
            headers = {"Accept-Version": "3"}
            r = requests.get(f"https://lookup.binlist.net/{bin_code}", headers=headers, timeout=10)
            if r.status_code != 200:
                continue
            d = r.json()
            bandeira = d.get("scheme", "Desconhecida").upper()
            marca = d.get("brand", "Desconhecida")
            tipo = d.get("type", "Desconhecido")
            prepaid = "Sim" if d.get("prepaid") else "Não"
            bank = d.get("bank") or {}
            nome_banco = bank.get("name", "Desconhecido")
            country = d.get("country") or {}
            nome_pais = country.get("name", "Desconhecido")
            resposta_final += (
                f"\n💳 *BIN:* `{bin_code}`\n"
                f"🏳️ *Bandeira:* {bandeira}\n"
                f"🏷️ *Marca:* {marca}\n"
                f"📦 *Tipo:* {tipo} | *Pré-pago:* {prepaid}\n"
                f"🏦 *Banco:* {nome_banco}\n"
                f"🌍 *País:* {nome_pais}\n"
                f"━━━━━━━━━━━━━━"
                )
        
        bot.reply_to(message, resposta_final or "❌ BIN não encontrada ou inválida.\nUse 6 dígitos, exemplo: /bin 529982", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"Erro: {e}")
        def comprar_pelo_botao(uid, cod_key):
    pr = get_produto(cod_key)
    if not pr:
        bot.send_message(uid, "❌ Produto não existe mais"); return

@bot.message_handler(commands=['addbin'])
def addbin_cmd(message):
    if message.from_user.id != ID_DONO:
        bot.reply_to(message, "Você não pode adicionar BINs.")
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "Uso: /addbin 0829")
        return
    for b in parts[1:]:
        b = b.strip()[:8]
        if b.isdigit() and b not in MINHAS_BINS:
            MINHAS_BINS.append(b)
    salvar_bins(MINHAS_BINS)
    bot.reply_to(message, f"✅ Adicionada! Lista atual:\n`{' '.join(MINHAS_BINS)}`", parse_mode="Markdown")

@bot.message_handler(commands=['delbin'])
def delbin_cmd(message):
    if message.from_user.id != ID_DONO:
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "Uso: /delbin 0829")
        return
    for b in parts[1:]:
        if b in MINHAS_BINS:
            MINHAS_BINS.remove(b)
    salvar_bins(MINHAS_BINS)
    bot.reply_to(message, f"🗑️ Removida! Lista atual:\n`{' '.join(MINHAS_BINS)}`", parse_mode="Markdown")
if __name__ == "__main__":
    bot.remove_webhook()
    
    threading.Thread(target=verificar_pendentes, daemon=True).start()
    if RAILWAY_URL:
        bot.set_webhook(url=f"{RAILWAY_URL}/webhook")
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
