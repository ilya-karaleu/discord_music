import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import asyncio
import os
from keep_alive import keep_alive

intents = discord.Intents.default()
bot = commands.Bot(command_prefix='!', intents=intents)

# Словарь для хранения очередей каждого сервера (guild_id: [список треков])
queues = {}

# Настройки yt-dlp для быстрого сбора ссылок из плейлистов
ytdl_format_options_search = {
    'format': 'bestaudio/best',
    'extract_flat': 'in_playlist', 
    'noplaylist': False,           
    'ignoreerrors': True,          
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '::',
    'playlistend': 50,  # Ограничение джемов и плейлистов до 50 треков
    'extractor_args': {
        'youtube': {
            # Притворяемся Android-устройством или телевизором
            'player_client': ['android', 'ios', 'tv', 'web'],
            # Ускоряем запросы, пропуская загрузку лишних конфигов YouTube
            'player_skip': ['webpage', 'configs']
        }
    },
    'proxy': 'http://45.10.163.12'
}

ytdl_format_options_stream = dict(ytdl_format_options_search)
ytdl_format_options_stream['extract_flat'] = False
ytdl_format_options_stream['noplaylist'] = True 

# FFmpeg с автоматической нормализацией громкости (loudnorm)
ffmpeg_options = {
    'options': '-vn -af "loudnorm=I=-16:LRA=11:TP=-1.5"',
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
}

ytdl_search = yt_dlp.YoutubeDL(ytdl_format_options_search)
ytdl_stream = yt_dlp.YoutubeDL(ytdl_format_options_stream)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl_stream.extract_info(url, download=False))
        filename = data['url']
        
        # Автоматический выбор ffmpeg: локальный файл для Windows или системный для Render/Linux
        ffmpeg_executable = 'ffmpeg.exe' if os.path.exists('ffmpeg.exe') else 'ffmpeg'
        
        return cls(discord.FFmpegPCMAudio(filename, executable=ffmpeg_executable, **ffmpeg_options), data=data)

async def play_next(interaction: discord.Interaction, voice_client: discord.VoiceClient):
    guild_id = interaction.guild.id
    if guild_id in queues and len(queues[guild_id]) > 0:
        next_track = queues[guild_id].pop(0)
        try:
            player = await YTDLSource.from_url(next_track['url'], loop=bot.loop)
            voice_client.play(player, after=lambda e: bot.loop.create_task(play_next(interaction, voice_client)))
            await interaction.channel.send(f'🎶 Сейчас играет: **{player.title}**')
        except Exception as e:
            print(f"Ошибка воспроизведения: {e}")
            bot.loop.create_task(play_next(interaction, voice_client))
    else:
        await interaction.channel.send("Очередь пуста. Воспроизведение завершено 🎵")

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f'Бот {bot.user} успешно запущен и слеш-команды загружены!')

@bot.tree.command(name="play", description="Воспроизводит трек, плейлист или микс с YouTube")
async def play(interaction: discord.Interaction, url: str):
    if not interaction.user.voice:
        await interaction.response.send_message("Ты должен находиться в голосовом канале!", ephemeral=True)
        return

    await interaction.response.defer()

    channel = interaction.user.voice.channel
    voice_client = interaction.guild.voice_client
    
    if not voice_client:
        voice_client = await channel.connect()

    guild_id = interaction.guild.id
    if guild_id not in queues:
        queues[guild_id] = []

    try:
        loop = bot.loop
        data = await loop.run_in_executor(None, lambda: ytdl_search.extract_info(url, download=False))

        # --- ДОБАВЛЯЕМ ЭТУ ПРОВЕРКУ ---
        if data is None:
            await interaction.followup.send("❌ Не удалось загрузить трек. Возможно, видео скрыто, удалено, или YouTube временно заблокировал запрос.")
            return
        # ------------------------------

        if 'entries' in data:
            entries = [e for e in data['entries'] if e][:50]
            for entry in entries:
                track_url = entry.get('url') or f"https://www.youtube.com/watch?v={entry.get('id')}"
                queues[guild_id].append({'title': entry.get('title'), 'url': track_url})
            
            await interaction.followup.send(f'📁 Добавлено в очередь **{len(entries)}** треков из плейлиста/джема **{data.get("title", "YouTube Mix")}**!')
        else:
            track_url = data.get('webpage_url', url)
            queues[guild_id].append({'title': data.get('title'), 'url': track_url})
            await interaction.followup.send(f'🎵 Добавлено в очередь: **{data.get("title")}**')

        if not voice_client.is_playing():
            bot.loop.create_task(play_next(interaction, voice_client))

    except Exception as e:
        await interaction.followup.send(f"Произошла ошибка при загрузке: {e}")


@bot.tree.command(name="skip", description="Пропустить текущий трек")
async def skip(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.stop() 
        await interaction.response.send_message("Трек пропущен ⏭️")
    else:
        await interaction.response.send_message("Сейчас ничего не играет.")

@bot.tree.command(name="skipall", description="Очистить всю очередь и остановить плеер")
async def skipall(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    guild_id = interaction.guild.id

    # Очищаем очередь для сервера
    if guild_id in queues:
        count = len(queues[guild_id])
        queues[guild_id] = []
    else:
        count = 0

    if voice_client:
        if voice_client.is_playing():
            voice_client.stop()
        await voice_client.disconnect()
        await interaction.response.send_message(f"Очередь очищена (удалено треков: {count}). Бот отключен 🛑")
    else:
        await interaction.response.send_message("Бот не находится в голосовом канале, но очередь очищена.")

@bot.tree.command(name="stop", description="Остановить музыку, очистить очередь и выйти")
async def stop(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    guild_id = interaction.guild.id

    # Очищаем очередь
    if guild_id in queues:
        count = len(queues[guild_id])
        queues[guild_id] = []
    else:
        count = 0

    if voice_client:
        if voice_client.is_playing():
            voice_client.stop()
        await voice_client.disconnect()
        await interaction.response.send_message(f"Очередь очищена (удалено треков: {count}). Бот отключен 🛑")
    else:
        await interaction.response.send_message("Бот не находится в голосовом канале.")

# Запуск сервера и бота
keep_alive()

bot.run(os.environ.get('DISCORD_TOKEN'))