import asyncio
from pyrogram import Client, filters
import requests

# Configurações do Bot e Gateway
API_ID = "GLADE_API"
API_HASH = "GLADE_API"
BOT_TOKEN = "ADMIN_ID"
GLADE_PAY_API_URL = "GLADE_URL" # URL hipotética da API
GLADE_PAY_TOKEN = "GLADE_API"

app = Client("card_checker_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

def check_card_glade_pay(cc, mm, yy, cvv):
    """
    Realiza a tentativa de cobrança de 5 reais no gateway Glade Pay.
    """
    payload = {
        "amount": 5.00,
        "currency": "BRL",
        "card": {
            "number": cc,
            "exp_month": mm,
            "exp_year": yy,
            "cvv": cvv
        }
    }
    headers = {
        "Authorization": f"Bearer {GLADE_PAY_TOKEN}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(GLADE_PAY_API_URL, json=payload, headers=headers, timeout=10)
        # Verifica se o status da transação foi sucesso
        if response.status_code == 200 and response.json().get("status") == "succeeded":
            return "✅ LIVE"
        else:
            return f"❌ DEAD ({response.json().get('error', 'Erro desconhecido')})"
    except Exception as e:
        return f"⚠️ ERRO: {str(e)}"

@app.on_message(filters.command("chk"))
async def handle_chk(client, message):
    # Espera formato: /chk cc|mm|yy|cvv ou lista de cartões
    text = message.text.split(maxsplit=1)
    if len(text) < 2:
        await message.reply("Use: `/chk cc|mm|yy|cvv` ou envie uma lista de cartões.")
        return

    cards_list = text[1].split("\n")
    await message.reply(f"Processando {len(cards_list)} cartões... Aguarde.")
    
    results = []
    for card_data in cards_list:
        try:
            # Divide a linha por | ou : ou espaço
            parts = card_data.replace(":", "|").replace(" ", "|").split("|")
            if len(parts) >= 4:
                cc, mm, yy, cvv = parts[0], parts[1], parts[2], parts[3]
                # Ajuste de ano (YY para YYYY)
                if len(yy) == 2:
                    yy = "20" + yy
                
                status = check_card_glade_pay(cc, mm, yy, cvv)
                results.append(f"`{cc}|{mm}|{yy}|{cvv}` -> {status}")
            else:
                results.append(f"Linha inválida: {card_data}")
        except Exception:
            results.append(f"Erro ao processar: {card_data}")

    # Envia os resultados em blocos para evitar limite de caracteres do Telegram
    final_msg = "\n".join(results)
    if len(final_msg) > 4096:
        for i in range(0, len(final_msg), 4096):
            await message.reply(final_msg[i:i+4096])
    else:
        await message.reply(final_msg)

print("Bot iniciado...")
app.run()
