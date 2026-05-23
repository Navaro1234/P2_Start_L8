import os
import discord
from discord.ext import commands
from google import genai
from dotenv import load_dotenv  # Laad de dotenv bibliotheek

# Laad de variabelen uit het .env bestand
load_dotenv()

# Haal de geheime sleutels veilig op uit de omgevingsvariabelen
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Initialiseer de Google Gemini client met de veilige sleutel
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# Discord Intents instellen
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="$", intents=intents)


@bot.event
async def on_ready():
    print(f'We zijn ingelogd als {bot.user}')
    print("Tokens zijn succesvol en veilig geladen via .env!")


@bot.event
async def on_message(message):
    # Voorkom dat de bot op zichzelf reageert
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
                response = ai_client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=schone_tekst,
                    config={
                        "system_instruction": "Je bent een behulpzame Discord-assistent genaamd Aap."
                    }
                )
                if response.text:
                    await message.reply(response.text)
                else:
                    await message.send("Gemini gaf een leeg antwoord terug.")
            except Exception as e:
                print(f"!!! GEMINI FOUTMELDING !!!: {e}")
                await message.send("Er is een fout opgetreden bij het praten met Google Gemini.")
        return

        # Zorgt ervoor dat het $vraag commando hieronder blijft werken
    await bot.process_commands(message)


# --- HIER IS HET $VRAAG COMMANDO ---

@bot.command()
async def vraag(ctx, *, bericht: str):
    """Typ $vraag [jouw bericht] om gratis met Gemini te praten"""
    async with ctx.typing():
        try:
            response = ai_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=bericht,
                config={
                    "system_instruction": "Je bent een behulpzame Discord-assistent genaamd Aap."
                }
            )
            if response.text:
                await ctx.reply(response.text)
            else:
                await ctx.send("Gemini gaf een leeg antwoord terug.")
        except Exception as e:
            print(f"!!! GEMINI FOUTMELDING !!!: {e}")
            await ctx.send("Er is een fout opgetreden bij het praten met Google Gemini.")


# Start de bot met het veilige token
bot.run(DISCORD_TOKEN)
