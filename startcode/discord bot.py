import os
import json
import asyncio
import discord
from discord.ext import commands
from google import genai
from groq import AsyncGroq  # NIEUW: De asynchrone Groq bibliotheek
from dotenv import load_dotenv
from PIL import Image

# --- CONFIGURATIE EN BEHEERDERSGEGEVENS ---
ADMIN_USERNAME = "navaroke2512"
APPDATA_DIR = os.path.join(os.getenv("LOCALAPPDATA"), "Discord_bot")
ENV_FILE = os.path.join(APPDATA_DIR, ".env")

os.makedirs(APPDATA_DIR, exist_ok=True)


def configureer_env():
    if os.path.exists(ENV_FILE):
        load_dotenv(ENV_FILE)
        if os.getenv("DISCORD_TOKEN") and os.getenv("GEMINI_API_KEY") and os.getenv("GROQ_API_KEY"):
            return

    print("=" * 60)
    print(f"  ⚠️ GEEN VOLLEDIGE CONFIGURATIE GEVONDEN IN: {APPDATA_DIR}")
    print("  Vul hieronder je gegevens in om de bot in te stellen.")
    print("=" * 60)

    discord_token = input("Plak je Discord Token en druk op Enter: ").strip()
    gemini_key = input("Plak je Google Gemini API Key en druk op Enter: ").strip()
    groq_key = input("Plak je Groq API Key (gsk_...) en druk op Enter: ").strip()

    if not discord_token or not gemini_key or not groq_key:
        print("\n❌ Fout: Alle sleutels zijn verplicht!")
        exit(1)

    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write(f"DISCORD_TOKEN={discord_token}\n")
        f.write(f"GEMINI_API_KEY={gemini_key}\n")
        f.write(f"GROQ_API_KEY={groq_key}\n")

    print(f"\n✅ Het .env bestand is succesvol opgeslagen in:\n   {ENV_FILE}")
    print("=" * 60 + "\n")
    load_dotenv(ENV_FILE)


configureer_env()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Initialiseer beide online AI-clients
ai_client = genai.Client(api_key=GEMINI_API_KEY)
groq_client = AsyncGroq(api_key=GROQ_API_KEY)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="$", intents=intents)

# --- LOCALE MAPPEN EN GEHEUGEN LOGICA PER GEBRUIKER ---
MAX_GEHEUGEN = 30


def haal_gebruiker_paden(username):
    gebruiker_map = os.path.join(APPDATA_DIR, username)
    geschiedenis_map = os.path.join(gebruiker_map, "chat_geschiedenis")
    json_bestand = os.path.join(geschiedenis_map, "geschiedenis.json")
    os.makedirs(geschiedenis_map, exist_ok=True)
    return json_bestand


def laad_gebruiker_geschiedenis(username):
    json_path = haal_gebruiker_paden(username)
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Fout bij laden JSON van {username}: {e}")
    return []


def bewaar_gebruiker_geschiedenis(username, geschiedenis_lijst):
    json_path = haal_gebruiker_paden(username)
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(geschiedenis_lijst, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Fout bij opslaan JSON van {username}: {e}")


def voeg_toe_aan_geheugen(username, rol, tekst):
    geschiedenis = laad_gebruiker_geschiedenis(username)
    api_rol = "user" if rol == "user" else "assistant"
    geschiedenis.append({"role": api_rol, "content": tekst})

    if len(geschiedenis) > MAX_GEHEUGEN:
        geschiedenis.pop(0)

    bewaar_gebruiker_geschiedenis(username, geschiedenis)


async def stuur_lang_bericht(bestemming, tekst):
    if not tekst:
        await bestemming.send("De AI gaf een leeg antwoord terug.")
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
    print("Aap gebruikt GOOGLE GEMINI als hoofd-AI en GROQ als gratis back-up!")


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    is_dm = isinstance(message.channel, discord.DMChannel)

    if message.content.lower() == 'ping':
        await message.reply('pong')
        return
    elif message.content.lower() == '/admin':
        await message.reply('navaroke2512 is de admin. Land = :flag_be:.')
        return
    elif 'lief' in message.content.lower():
        await message.add_reaction('🗑️')

    if bot.user in message.mentions or is_dm:
        schone_tekst = message.content.replace(f'<@{bot.user.id}>', '').strip()

        if schone_tekst.startswith("$"):
            await bot.process_commands(message)
            return

        async with message.channel.typing():
            lokaal_pad = None
            pil_afbeelding = None

            if message.attachments:
                bijlage = message.attachments
                if any(bijlage.filename.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                    gebruiker_map = os.path.join(APPDATA_DIR, message.author.name)
                    os.makedirs(gebruiker_map, exist_ok=True)
                    lokaal_pad = os.path.join(gebruiker_map, bijlage.filename)
                    await bijlage.save(lokaal_pad)
                    try:
                        pil_afbeelding = Image.open(lokaal_pad)
                    except Exception as img_err:
                        print(f"Fout bij openen afbeelding met PIL: {img_err}")

            voeg_toe_aan_geheugen(message.author.name, "user", schone_tekst)
            geschiedenis = laad_gebruiker_geschiedenis(message.author.name)
            ai_reply = ""

            # --- STRATEGIE 1: PROBEER EERST GOOGLE GEMINI 2.0 FLASH ---
            try:
                google_content = []
                if pil_afbeelding:
                    google_content.append(pil_afbeelding)
                    google_content.append(
                        schone_tekst if schone_tekst else "Beschrijf deze afbeelding in het Nederlands.")
                else:
                    for msg in geschiedenis:
                        google_role = "user" if msg["role"] == "user" else "model"
                        google_content.append({"role": google_role, "parts": [{"text": msg["content"]}]})

                response = ai_client.models.generate_content(
                    model='gemini-2.0-flash',
                    contents=google_content,
                    config={
                        "system_instruction": f"Je bent een gezellige, slimme Discord-assistent genaamd Aap. Je praat met {message.author.name}. Antwoord ALTIJD in vloeiend, kort en bondig Nederlands (maximaal 3 zinnen)."}
                )
                ai_reply = response.text
                print(f"☁️ Beantwoord via GOOGLE GEMINI voor {message.author.name}")
            except Exception as gemini_err:
                print(f"⚠️ Google Gemini limiet bereikt of fout, schakelt over naar Groq... ({gemini_err})")

            # --- STRATEGIE 2: FALLBACK NAAR GRATIS GROQ (Alleen tekst) ---
            if not ai_reply and not pil_afbeelding:
                try:
                    groq_messages = [{"role": "system",
                                      "content": f"Je bent een gezellige, slimme Discord-assistent genaamd Aap. Je praat met {message.author.name}. Antwoord ALTIJD in vloeiend, kort en bondig Nederlands (maximaal 3 zinnen)."}] + geschiedenis

                    # We gebruiken het razendsnelle en gratis llama-3.3 model op Groq
                    response = await groq_client.chat.completions.create(
                        model="llama-3.3-70b-versatile",
                        messages=groq_messages
                    )
                    ai_reply = response.choices[0].message.content
                    print(f"🚀 Beantwoord via GRATIS GROQ FALLBACK (Llama 3.3) voor {message.author.name}")
                except Exception as groq_err:
                    print(f"!!! CRITIEKE APOCALYPS, BEIDE ENGINE STUK !!!: {groq_err}")
                    await message.channel.send(
                        "Het lukt me nu even niet om na te denken lokaal of online. Probeer het zo weer!")
                    return

            if not ai_reply and pil_afbeelding:
                await message.channel.send(
                    "Mijn Google-limiet voor foto's is op, en mijn back-up engine ondersteunt helaas geen afbeeldingen. Probeer het later nog eens!")
                return

            voeg_toe_aan_geheugen(message.author.name, "model", ai_reply)
            if pil_afbeelding:
                await message.channel.send(f"*📸 Afbeelding opgeslagen in je persoonlijke AppData map!*")

            await stuur_lang_bericht(message, ai_reply)
        return

    await bot.process_commands(message)


# --- COMMANDO'S ---

@bot.command()
async def vraag(ctx, *, bericht: str):
    """Typ $vraag [jouw bericht] om te praten via Google of Groq Fallback"""
    async with ctx.typing():
        voeg_toe_aan_geheugen(ctx.author.name, "user", bericht)
        geschiedenis = laad_gebruiker_geschiedenis(ctx.author.name)
        ai_reply = ""

        try:
            google_content = []
            for msg in geschiedenis:
                google_role = "user" if msg["role"] == "user" else "model"
                google_content.append({"role": google_role, "parts": [{"text": msg["content"]}]})

            response = ai_client.models.generate_content(
                model='gemini-2.0-flash',
                contents=google_content,
                config={
                    "system_instruction": "Je bent een gezellige, slimme Discord-assistent genaamd Aap. Antwoord kort in het Nederlands."}
            )
            ai_reply = response.text
            print("☁️ $vraag beantwoord via GOOGLE GEMINI")
        except Exception:
            try:
                groq_messages = [{"role": "system",
                                  "content": "Je bent een gezellige, slimme Discord-assistent genaamd Aap. Antwoord kort in het Nederlands."}] + geschiedenis
                response = await groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=groq_messages
                )
                ai_reply = response.choices.message.content
                print("🚀 $vraag beantwoord via GRATIS GROQ FALLBACK")
            except Exception as groq_err:
                print(f"!!! GROQ FOUT !!!: {groq_err}")
                await ctx.send("Zowel Google als Groq zijn momenteel onbereikbaar.")
                return

        voeg_toe_aan_geheugen(ctx.author.name, "model", ai_reply)
        await stuur_lang_bericht(ctx, ai_reply)


@bot.command()
async def stats(ctx):
    """Toont statistieken van jouw eigen mapje. Alleen voor de admin in DM."""
    if not isinstance(ctx.channel, discord.DMChannel):
        await ctx.reply("❌ Dit commando kan alleen in een privéchat (DM) worden gebruikt.")
        return

    if ctx.author.name != ADMIN_USERNAME:
        await ctx.reply("🔒 Geen toegang.")
        return

    geschiedenis = laad_gebruiker_geschiedenis(ctx.author.name)
    eigen_json_pad = haal_gebruiker_paden(ctx.author.name)

    embed = discord.Embed(title="📊 Aap Bot Redundant Systeeminformatie", color=discord.Color.blue())
    embed.add_field(name="Jouw opgeslagen berichten", value=f"💬 {len(geschiedenis)} / {MAX_GEHEUGEN}", inline=False)
    embed.add_field(name="Hoofd AI-engine", value="☁️ Google Cloud (Gemini 2.0 Flash)", inline=False)
    embed.add_field(name="Nood-back-up AI-engine", value="🚀 Groq Cloud (Llama 3.3 70B)", inline=False)
    embed.add_field(name="Jouw persoonlijke geschiedenis-locatie", value=f"📂 `{eigen_json_pad}`", inline=False)

    await ctx.send(embed=embed)


@bot.command()
async def clear(ctx):
    """Wist jouw eigen persoonlijke geschiedenis.json"""
    json_path = haal_gebruiker_paden(ctx.author.name)
    if os.path.exists(json_path):
        os.remove(json_path)
        await ctx.reply("🧹 Jouw persoonlijke `geschiedenis.json` is volledig verwijderd!")
    else:
        await ctx.reply("Je had nog geen geschiedenis-bestand opgeslagen.")


# Start de bot
bot.run(DISCORD_TOKEN)
