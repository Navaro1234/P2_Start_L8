import os
import json
import asyncio
import sqlite3
import uuid
import requests
import discord
from discord.ext import commands, tasks
from google import genai
from groq import AsyncGroq
from dotenv import load_dotenv
import httpx
from PIL import Image

# --- CONFIGURATIE EN BEHEERDERSGEGEVENS ---
ADMIN_USERNAME = "navaroke2512"
APPDATA_DIR = os.path.join(os.getenv("LOCALAPPDATA"), "Discord_bot")
ENV_FILE = os.path.join(APPDATA_DIR, ".env")
DB_FILE = os.path.join(APPDATA_DIR, "economie_v2.db")

RETRIEVE_URL = "https://ynbxdmdvqhuzxofanhke.supabase.co/functions/v1/config-retrieve"
POLL_URL = "https://ynbxdmdvqhuzxofanhke.supabase.co/functions/v1/bot-commands"

os.makedirs(APPDATA_DIR, exist_ok=True)

DISCORD_TOKEN = None
GEMINI_API_KEY = None
GROQ_API_KEY = None
LOVABLE_TOKEN = None
TARGET_CHANNEL_ID = None


def configureer_env_en_cloud():
    global DISCORD_TOKEN, GEMINI_API_KEY, GROQ_API_KEY, LOVABLE_TOKEN, TARGET_CHANNEL_ID
    if os.path.exists(ENV_FILE):
        load_dotenv(ENV_FILE)
        LOVABLE_TOKEN = os.getenv("LOVABLE_TOKEN")
        TARGET_CHANNEL_ID = os.getenv("CHANNEL_ID")

    if not LOVABLE_TOKEN or not TARGET_CHANNEL_ID:
        print("=" * 60)
        print("  ☁️ WELKOM BIJ DE AAP BOT CLOUD CONFIGURATIE (LOVABLE)")
        print("=" * 60)
        target_channel = input("Voer het Discord Kanaal-ID in waar Admin-berichten moeten komen: ").strip()
        lovable_input = input("Plak je Lovable Token (lovable_usr_...) en druk op Enter: ").strip()

        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.write(f"LOVABLE_TOKEN={lovable_input}\n")
            f.write(f"CHANNEL_ID={target_channel}\n")

        load_dotenv(ENV_FILE)
        LOVABLE_TOKEN = lovable_input
        TARGET_CHANNEL_ID = target_channel

    try:
        response = httpx.post(RETRIEVE_URL, json={"lovable_token": LOVABLE_TOKEN}, timeout=10.0)
        data = response.json()
        if data.get("status") == "success" or "discord_token" in data:
            DISCORD_TOKEN = data.get("discord_token")
            GEMINI_API_KEY = data.get("gemini_api_key")
            GROQ_API_KEY = data.get("groq_api_key")
        else:
            print(f"❌ Cloud API Fout: {data.get('message', 'Ongeldige token')}")
            exit(1)
    except Exception as e:
        print(f"❌ Fout bij verbinden met Lovable Cloud: {e}")
        exit(1)


configureer_env_en_cloud()


# --- SQLITE DATABASE INITIALISATIE ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS profielen (
            user_id TEXT PRIMARY KEY,
            username TEXT,
            credits INTEGER DEFAULT 0,
            actieve_titel TEXT DEFAULT 'Geen',
            geregistreerd INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bezeten_titels (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, titel TEXT, UNIQUE(user_id, titel)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS geld_aanvragen (
            user_id TEXT PRIMARY KEY, username TEXT, aangevraagd_bedrag INTEGER
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS registratie_keys (
            reg_key TEXT PRIMARY KEY, user_id TEXT, username TEXT, goedgekeurd INTEGER DEFAULT 0
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tracked_channels (
            channel_id TEXT PRIMARY KEY, channel_name TEXT, channel_type TEXT
        )
    ''')

    conn.commit()
    conn.close()


init_db()


# Database Hulpfuncties
def SQL_gebruiker_bestaat(user_id, username):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT geregistreerd, is_banned FROM profielen WHERE user_id = ?', (str(user_id),))
    result = cursor.fetchone()
    if not result:
        status = 1 if username == ADMIN_USERNAME else 0
        cursor.execute('INSERT INTO profielen (user_id, username, credits, geregistreerd) VALUES (?, ?, ?, ?)',
                       (str(user_id), username, 0, status))
        conn.commit()
        return status, 0
    return result


def SQL_check_ban(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT is_banned FROM profielen WHERE user_id = ?', (str(user_id),))
    res = cursor.fetchone()
    conn.close()
    return res[0] if res else 0


def SQL_credits_erbij(user_id, aantal):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('UPDATE profielen SET credits = credits + ? WHERE user_id = ?', (aantal, str(user_id)))
    conn.commit()
    conn.close()


def SQL_haal_profiel(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT credits, actieve_titel FROM profielen WHERE user_id = ?', (str(user_id),))
    res = cursor.fetchone()
    conn.close()
    return res if res else (0, 'Geen')


# AI Initialisatie
ai_client = genai.Client(api_key=GEMINI_API_KEY)
groq_client = AsyncGroq(api_key=GROQ_API_KEY)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="$", intents=intents)

MAX_GEHEUGEN = 30


# --- PROFIELFOTO DOWNLOADER ---
def download_avatar(user):
    gebruiker_map = os.path.join(APPDATA_DIR, user.name)
    os.makedirs(gebruiker_map, exist_ok=True)
    target_file = os.path.join(gebruiker_map, "profilepicture.png")

    if user.avatar:
        try:
            r = requests.get(user.avatar.url, stream=True)
            if r.status_code == 200:
                with open(target_file, 'wb') as f:
                    for chunk in r.iter_content(1024):
                        f.write(chunk)
        except Exception as e:
            print(f"Kon avatar niet downloaden: {e}")


def haal_gebruiker_pad(username):
    gebruiker_map = os.path.join(APPDATA_DIR, username)
    geschiedenis_map = os.path.join(gebruiker_map, "chat_geschiedenis")
    os.makedirs(geschiedenis_map, exist_ok=True)
    return os.path.join(geschiedenis_map, "geschiedenis.json")


def laad_gebruiker_geschiedenis(username):
    json_path = haal_gebruiker_pad(username)
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def bewaar_gebruiker_geschiedenis(username, geschiedenis_lijst):
    json_path = haal_gebruiker_pad(username)
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(geschiedenis_lijst, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Fout bij opslaan JSON: {e}")


def voeg_toe_aan_geheugen(username, rol, tekst):
    text_only = tekst
    if isinstance(tekst, list):
        # Mocht er per ongeluk een fotolijst binnenkomen, pak de tekst
        text_only = next((item for item in tekst if isinstance(item, str)), "")
    geschiedenis = laad_gebruiker_geschiedenis(username)
    geschiedenis.append({"role": "user" if rol == "user" else "assistant", "content": text_only})
    if len(geschiedenis) > MAX_GEHEUGEN: geschiedenis.pop(0)
    bewaar_gebruiker_geschiedenis(username, geschiedenis)


async def stuur_lang_bericht(bestemming, tekst):
    if not tekst: return
    for i in range(0, len(tekst), 1900):
        if isinstance(bestemming, discord.Message):
            await bestemming.reply(tekst[i:i + 1900])
        else:
            await bestemming.send(tekst[i:i + 1900])


@tasks.loop(seconds=5.0)
async def poll_admin_commands():
    try:
        headers = {"x-lovable-token": LOVABLE_TOKEN}
        async with httpx.AsyncClient() as client:
            response = await client.get(POLL_URL, headers=headers, timeout=4.0)
            if response.status_code == 200:
                data = response.json()
                if data.get("action") == "send_chat" and data.get("message"):
                    channel = bot.get_channel(int(TARGET_CHANNEL_ID))
                    if channel: await channel.send(f"📢 **[Admin Bericht]:** {data.get('message')}")
    except Exception:
        pass


@bot.event
async def on_ready():
    print(f'We zijn succesvol ingelogd als {bot.user}')
    poll_admin_commands.start()

    # --- KANALEN TRACKER ---
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    for guild in bot.guilds:
        for channel in guild.channels:
            ch_type = "Tekst" if isinstance(channel, discord.TextChannel) else "Spraak" if isinstance(channel,
                                                                                                      discord.VoiceChannel) else "Categorie"
            cursor.execute(
                'INSERT OR REPLACE INTO tracked_channels (channel_id, channel_name, channel_type) VALUES (?, ?, ?)',
                (str(channel.id), channel.name, ch_type))
    conn.commit()
    conn.close()
    print("📂 Alle serverkanalen en ID's succesvol opgeslagen in SQL!")


@bot.event
async def on_message(message):
    if message.author == bot.user: return
    is_dm = isinstance(message.channel, discord.DMChannel)

    if SQL_check_ban(message.author.id):
        return

    geregistreerd, _ = SQL_gebruiker_bestaat(message.author.id, message.author.name)
    download_avatar(message.author)

    if message.content.startswith("$registreer") or message.content.startswith("$toegang"):
        await bot.process_commands(message)
        return

    if not geregistreerd and not is_dm:
        await message.reply(
            "🔒 Je hebt nog geen toegang tot deze bot. Typ `$toegang` om een activatiekey aan te vragen!")
        return

    if message.content.lower() == 'ping':
        await message.reply('pong')
        return
    elif message.content.lower() == '/admin':
        await message.reply('navaroke2512 is de admin. Land = :flag_be:.')
        return
    elif 'lief' in message.content.lower():
        await message.add_reaction('🗑️')

        # 2. AI AFHANDELING
        if bot.user in message.mentions or is_dm:
            schone_tekst = message.content.replace(f'<@{bot.user.id}>', '').strip()
            if schone_tekst.startswith("$"):
                await bot.process_commands(message)
                return

            # --- AUTOMATISCHE AI MODERATIE FILTER ---
            try:
                mod_check = ai_client.models.generate_content(
                    model='gemini-2.0-flash',
                    contents=f"Beoordeel of deze tekst racistisch, extreem haatdragend of zwaar discriminerend is. Antwoord met exact 1 woord: JA of NEE. Tekst: {schone_tekst}"
                )
                if "JA" in mod_check.text.upper() and not is_dm:
                    print(f"🚨 RACISME DETECTIE! Overtreder: {message.author.name}")

                    conn = sqlite3.connect(DB_FILE)
                    cursor = conn.cursor()
                    cursor.execute('UPDATE profielen SET is_banned = 1 WHERE user_id = ?', (str(message.author.id),))
                    conn.commit()
                    conn.close()

                    try:
                        await message.author.send(
                            "🚨 Je bent permanent verbannen uit de server wegens het overtreden van de gedragsregels (Racisme/Haatzaaien).")
                        await message.guild.ban(message.author,
                                                reason="Automatische AI Ban: Racisme/Haatdragende content gedetecteerd.")
                        await message.channel.send(
                            f"🛡️ **Aap Mod-Systeem:** Gebruiker `{message.author.display_name}` is voorgoed verbannen wegens racistische opmerkingen.")
                    except Exception as ban_err:
                        print(f"Kon lid niet bannen: {ban_err}")
                    return
            except Exception:
                pass

            async with message.channel.typing():
                SQL_credits_erbij(message.author.id, 10)
                voeg_toe_aan_geheugen(message.author.name, "user", schone_tekst)
                geschiedenis = laad_gebruiker_geschiedenis(message.author.name)
                credits, titel = SQL_haal_profiel(message.author.id)

                try:
                    google_content = []
                    for msg in geschiedenis: google_content.append(
                        {"role": "user" if msg["role"] == "user" else "model", "parts": [{"text": msg["content"]}]})
                    response = ai_client.models.generate_content(
                        model='gemini-2.0-flash', contents=google_content,
                        config={
                            "system_instruction": f"Je bent assistent Aap. Je praat met {message.author.display_name} (Titel: {titel}). Antwoord kort in het Nederlands (max 3 zinnen)."}
                    )
                    ai_reply = response.text
                except Exception:
                    try:
                        groq_messages = [{"role": "system",
                                          "content": f"Je bent assistent Aap. Je praat met {message.author.display_name} (Titel: {titel}). Antwoord kort."}]
                        for msg in geschiedenis:
                            groq_messages.append(
                                {"role": "user" if msg["role"] == "user" else "assistant", "content": msg["content"]})
                        res = await groq_client.chat.completions.create(model="llama-3.3-70b-versatile",
                                                                        messages=groq_messages)
                        ai_reply = res.choices.message.content
                    except Exception:
                        await message.channel.send("AI overbelast. (+10 credits ontvangen!)")
                        return

                if ai_reply:
                    voeg_toe_aan_geheugen(message.author.name, "model", ai_reply)
                    await stuur_lang_bericht(message, ai_reply)
            return

        await bot.process_commands(message)

    # --- ACTIVATIE KEYS COMMANDO'S ---
    @bot.command(name="toegang")
    async def toegang(ctx):
        """Vraag een activatiekey aan"""
        if isinstance(ctx.channel, discord.DMChannel): return
        unieke_key = f"AAP-{str(uuid.uuid4())[:8].upper()}"

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('INSERT INTO registratie_keys (reg_key, user_id, username) VALUES (?, ?, ?)',
                       (unieke_key, str(ctx.author.id), ctx.author.name))
        conn.commit()
        conn.close()

        await ctx.reply("✉️ Je aanvraag is verstuurd naar de admin!")

        for guild in bot.guilds:
            admin_member = discord.utils.get(guild.members, name=ADMIN_USERNAME)
            if admin_member:
                await admin_member.send(
                    f"🔑 Toegangsaanvraag van `{ctx.author.name}`. Typ hier in DM:\n`$verifieer {ctx.author.name} {unieke_key}`")
                break

    @bot.command(name="verifieer")
    async def verifieer(ctx, doel_naam: str, unieke_key: str):
        """(ADMIN IN DM) Keurt de aanvraag goed"""
        if not isinstance(ctx.channel, discord.DMChannel) or ctx.author.name != ADMIN_USERNAME: return

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('UPDATE registratie_keys SET goedgekeurd = 1 WHERE username = ? AND reg_key = ?',
                       (doel_naam, unieke_key))
        cursor.execute('SELECT user_id FROM registratie_keys WHERE username = ?', (doel_naam,))
        res = cursor.fetchone()
        conn.close()

        if res:
            user_obj = bot.get_user(int(res))
            if user_obj:
                await user_obj.send(
                    f"🔑 Je key is goedgekeurd! Activeer hem in de server met:\n`$registreer key=\"{unieke_key}\"`")
                await ctx.reply(f"✅ Key verzonden naar `{doel_naam}`.")

    @bot.command(name="registreer")
    async def registreer(ctx, *, key: str):
        """Activeer de bot met je key"""
        schone_key = key.replace('key=', '').replace('"', '').strip()

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT goedgekeurd FROM registratie_keys WHERE user_id = ? AND reg_key = ?',
                       (str(ctx.author.id), schone_key))
        result = cursor.fetchone()

        if result and result == 1:
            cursor.execute('UPDATE profielen SET geregistreerd = 1 WHERE user_id = ?', (str(ctx.author.id),))
            cursor.execute('DELETE FROM registratie_keys WHERE user_id = ?', (str(ctx.author.id),))
            conn.commit()
            await ctx.reply("🎉 Je account is nu succesvol geactiveerd!")
        else:
            await ctx.reply("❌ Ongeldige of niet-goedgekeurde activatiesleutel.")
        conn.close()

    # --- ECONOMIE & ROLES SHOP ---
    @bot.command(name="shop")
    async def shop(ctx):
        embed = discord.Embed(title="🛒 Aap Bot Rollen Shop",
                              description="Koop een echte server-rol met credits! Typ `$koop [Titel]`.",
                              color=discord.Color.purple())
        embed.add_field(name="🥈 Rijk (Prijs: 100 credits)", value="Geeft je de zilveren 'Rijk' rol.", inline=False)
        embed.add_field(name="🥇 Elite (Prijs: 250 credits)", value="Koop de gouden 'Elite' rang.", inline=False)
        embed.add_field(name="👑 Koning (Prijs: 500 credits)", value="Word koning met een paarse kleur!", inline=False)
        await ctx.reply(embed=embed)

    @bot.command(name="koop")
    async def koop(ctx, titel: str):
        titel = titel.capitalize()
        prijzen = {"Rijk": 100, "Elite": 250, "Koning": 500}
        kleuren = {"Rijk": discord.Color.light_gray(), "Elite": discord.Color.gold(), "Koning": discord.Color.purple()}

        if titel not in prijzen: return
        prijs = prijzen[titel]
        credits, _ = SQL_haal_profiel(ctx.author.id)

        if credits < prijs:
            await ctx.reply(f"❌ Je hebt slechts {credits} / {prijs} credits.")
            return

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('UPDATE profielen SET credits = credits - ?, actieve_titel = ? WHERE user_id = ?',
                       (prijs, titel, str(ctx.author.id)))
        conn.commit()
        conn.close()

        guild = ctx.guild
        rol = discord.utils.get(guild.roles, name=titel)
        if not rol:
            rol = await guild.create_role(name=titel, color=kleuren[titel], hoist=True)

        await ctx.author.add_roles(rol)
        await ctx.reply(f"🎉 Je hebt de officiële server-rol **{titel}** gekregen!")

    @bot.command(name="money")
    async def money(ctx, bedrag: int):
        SQL_gebruiker_bestaat(ctx.author.id, ctx.author.name)
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO geld_aanvragen (user_id, username, aangevraagd_bedrag) VALUES (?, ?, ?)',
                       (str(ctx.author.id), ctx.author.name, bedrag))
        conn.commit()
        conn.close()
        await ctx.reply(f"✉️ Verzoek voor **{bedrag} credits** verzonden naar de admin.")

        for guild in bot.guilds:
            admin_member = discord.utils.get(guild.members, name=ADMIN_USERNAME)
            if admin_member:
                await admin_member.send(
                    f"🪙 Credit verzoek van `{ctx.author.name}` voor **{bedrag}** credits. Typ `$accept {ctx.author.name} {bedrag}` om goed te keuren.")

    @bot.command(name="accept")
    async def accept(ctx, doel_naam: str, bedrag: int):
        if not isinstance(ctx.channel, discord.DMChannel) or ctx.author.name != ADMIN_USERNAME: return
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM geld_aanvragen WHERE username = ?', (doel_naam,))
        res = cursor.fetchone()
        if res:
            cursor.execute('UPDATE profielen SET credits = credits + ? WHERE user_id = ?', (bedrag, res))
            cursor.execute('DELETE FROM geld_aanvragen WHERE user_id = ?', (res,))
            conn.commit()
            await ctx.reply(f"✅ Credits toegevoegd aan `{doel_naam}`.")
        conn.close()

    # ---🎙️ VOICE CHAT TTS ---
    @bot.command(name="praat")
    async def praat(ctx, *, tekst: str):
        """Laat de bot praten met een stem in een voice channel"""
        if not ctx.author.voice:
            await ctx.reply("❌ Je moet eerst in een spraakkanaal gaan zitten!")
            return

        spraak_kanaal = ctx.author.voice.channel
        vc = discord.utils.get(bot.voice_clients, guild=ctx.guild)
        if not vc:
            vc = await spraak_kanaal.connect()

        mp3_path = os.path.join(APPDATA_DIR, "tts_output.mp3")
        cmd = f'edge-tts --voice nl-NL-MaartenNeural --text "{tekst}" --write-media "{mp3_path}"'

        process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.DEVNULL,
                                                        stderr=asyncio.subprocess.DEVNULL)
        await process.wait()

        if os.path.exists(mp3_path):
            vc.play(discord.FFmpegPCMAudio(executable="ffmpeg", source=mp3_path))
bot.run(DISCORD_TOKEN)
