import os
import json
import discord
from discord.ext import commands
from google import genai
from dotenv import load_dotenv

# --- CENTRALE APPDATA MAP INSTELLEN ---
# Dit verwijst naar: C:\Users\<Gebruikersnaam>\AppData\Local\Discord_bot
APPDATA_DIR = os.path.join(os.getenv("LOCALAPPDATA"), "Discord_bot")
ENV_FILE = os.path.join(APPDATA_DIR, ".env")
JSON_FILE = os.path.join(APPDATA_DIR, "chat_geschiedenis.json")

# Zorg ervoor dat de map direct wordt aangemaakt als deze nog niet bestaat
os.makedirs(APPDATA_DIR, exist_ok=True)


def configureer_env():
    """Controleert .env in AppData en vraagt om invoer indien nodig"""
    if os.path.exists(ENV_FILE):
        load_dotenv(ENV_FILE)
        if os.getenv("DISCORD_TOKEN") and os.getenv("GEMINI_API_KEY"):
            return

    print("=" * 60)
    print(f"  ⚠️ GEEN CONFIGURATIE GEVONDEN IN: {APPDATA_DIR}")
    print("  Vul hieronder je gegevens in om de bot in te stellen.")
    print("=" * 60)

    discord_token = input("Plak je Discord Token en druk op Enter: ").strip()
    gemini_key = input("Plak je Google Gemini API Key en druk op Enter: ").strip()

    if not discord_token or not gemini_key:
        print("\n❌ Fout: Beide sleutels zijn verplicht!")
        exit(1)

    # Schrijf de sleutels rechtstreeks naar de AppData locatie
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write(f"DISCORD_TOKEN={discord_token}\n")
        f.write(f"GEMINI_API_KEY={gemini_key}\n")

    print(f"\n✅ Het .env bestand is succesvol opgeslagen in:\n   {ENV_FILE}")
    print("=" * 60 + "\n")
    load_dotenv(ENV_FILE)


# Voer de configuratie uit
configureer_env()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Initialiseer de Google Gemini client
ai_client = genai.Client(api_key=GEMINI_API_KEY)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="$", intents=intents)

# --- JSON GEHEUGEN FUNCTIES ---
MAX_GEHEUGEN = 10


def laad_geschiedenis_uit_json():
    """Laad de volledige geschiedenis in uit het JSON-bestand"""
    if os.path.exists(JSON_FILE):
        try:
            with open(JSON_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Fout bij laden JSON, we starten met leeg geheugen: {e}")
    return {}


def bewaar_geschiedenis_in_json(geschiedenis_data):
    """Schrijft de bijgewerkte geschiedenis weg naar het JSON-bestand"""
    try:
        with open(JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(geschiedenis_data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Fout bij opslaan naar JSON: {e}")


def voeg_toe_aan_geheugen(channel_id, rol, tekst):
    """Voegt een bericht toe en slaat dit direct permanent op in de JSON"""
    # Altijd de meest recente stand van zaken inlezen
    geheugen = laad_geschiedenis_uit_json()
    str_channel_id = str(channel_id)  # JSON keys moeten altijd strings zijn

    if str_channel_id not in geheugen:
        geheugen[str_channel_id] = []

    api_rol = "user" if rol == "user" else "model"
    geheugen[str_channel_id].append({"role": api_rol, "parts": [{"text": tekst}]})

    # Sloop het oudste bericht als de limiet is bereikt
    if len(geheugen[str_channel_id]) > MAX_GEHEUGEN:
        geheugen[str_channel_id].pop(0)

    # Schrijf meteen weg naar AppData
    bewaar_geschiedenis_in_json(geheugen)


def haal_geschiedenis_op(channel_id):
    """Haalt de geschiedenis op voor Gemini"""
    geheugen = laad_geschiedenis_uit_json()
    return geheugen.get(str(channel_id), [])


# HULPFUNCTIE: Knipt lange AI-antwoorden netjes op per 2000 tekens
async def stuur_lang_bericht(bestemming, tekst):
    if not tekst:
        await bestemming.send("Gemini gaf een leeg antwoord terug.")
        return
    for i in range(0, len(tekst), 1900):
        stukje = tekst[i:i + 1900]
        if isinstance(bestemming, discord.Message):
            await bestemming.reply(stukje)
        else:
            await bestemming.send(stukje)


@bot.event
async def on_ready():
    print(f'We zijn ingelogd als {bot.user}')
    print(f"Alles wordt succesvol geladen en opgeslagen in:\n-> {APPDATA_DIR}")


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # 1. Je originele handmatige reacties
    if message.content.lower() == 'ping':
        await message.reply('pong')
        return
    elif message.content.lower() == '/admin':
        await message.reply('navaroke2512 is de admin. Land = :flag_be:.')
        return
    elif 'lief' in message.content.lower():
        await message.add_reaction('🗑️')

    # 2. Reageren als de bot wordt getagd met @Aap
    if bot.user in message.mentions:
        schone_tekst = message.content.replace(f'<@{bot.user.id}>', '').strip()

        if not schone_tekst:
            await message.reply("Je hebt me getagd! Waar kan ik je vandaag mee helpen?")
            return

        async with message.channel.typing():
            try:
                voeg_toe_aan_geheugen(message.channel.id, "user", schone_tekst)
                volledige_geschiedenis = haal_geschiedenis_op(message.channel.id)

                response = ai_client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=volledige_geschiedenis,
                    config={
                        "system_instruction": "Je bent een behulpzame Discord-assistent genaamd Aap. Antwoord beknopt waar mogelijk."
                    }
                )

                voeg_toe_aan_geheugen(message.channel.id, "model", response.text)
                await stuur_lang_bericht(message, response.text)

            except Exception as e:
                print(f"!!! GEMINI FOUTMELDING !!!: {e}")
                await message.channel.send("Er is een fout opgetreden bij het praten met Google Gemini.")
        return

    await bot.process_commands(message)


# --- COMMANDO'S ---

@bot.command()
async def vraag(ctx, *, bericht: str):
    """Typ $vraag [jouw bericht] om gratis met Gemini te praten (onthoudt context via JSON)"""
    async with ctx.typing():
        try:
            voeg_toe_aan_geheugen(ctx.channel.id, "user", bericht)
            volledige_geschiedenis = haal_geschiedenis_op(ctx.channel.id)

            response = ai_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=volledige_geschiedenis,
                config={
                    "system_instruction": "Je bent een behulpzame Discord-assistent genaamd Aap. Antwoord beknopt waar mogelijk."
                }
            )

            voeg_toe_aan_geheugen(ctx.channel.id, "model", response.text)
            await stuur_lang_bericht(ctx, response.text)

        except Exception as e:
            print(f"!!! GEMINI FOUTMELDING !!!: {e}")
            await ctx.send("Er is een fout opgetreden bij het praten met Google Gemini.")


@bot.command()
async def clear(ctx):
    """Wist de opgeslagen chatgeschiedenis in de JSON voor dit kanaal"""
    geheugen = laad_geschiedenis_uit_json()
    str_channel_id = str(ctx.channel.id)

    if str_channel_id in geheugen:
        del geheugen[str_channel_id]
        bewaar_geschiedenis_in_json(geheugen)
        await ctx.reply("🧹 Het geheugen voor dit kanaal is gewist uit de JSON!")
    else:
        await ctx.reply("Er was nog geen geschiedenis opgeslagen voor dit kanaal.")


# Start de bot
bot.run(DISCORD_TOKEN)
