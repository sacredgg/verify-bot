import asyncio
import json
import os
from pathlib import Path

import aiohttp
import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
WEB_PORT = int(os.getenv("PORT", os.getenv("WEB_PORT", "8080")))
NEW_ACCOUNT_DAYS = float(os.getenv("NEW_ACCOUNT_DAYS", "0"))

CONFIG_FILE = Path(os.getenv("CONFIG_PATH", str(Path(__file__).resolve().parent / "config.json")))

GREEN = 0x57F287
RED = 0xED4245
BLURPLE = 0x5865F2

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"verify_role_id": None, "verify_channel_id": None}


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)


async def handle_root(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "bot": str(bot.user)})


async def keep_awake():
    """Пингует собственный домен, чтобы бесплатный хостинг не «усыплял» бота."""
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
    if not domain:
        return
    url = domain if domain.startswith("http") else f"https://{domain}"
    print(f"Keep-awake: пингую {url} каждые 5 минут")
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    print(f"Keep-awake: пинг {resp.status}")
        except Exception as exc:
            print(f"Keep-awake: ошибка {exc}")
        await asyncio.sleep(300)


class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Пройти верификацию",
        style=discord.ButtonStyle.success,
        custom_id="verify:button",
    )
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = load_config()
        role_id = cfg.get("verify_role_id")
        if not role_id:
            await interaction.response.send_message(
                "Роль верификации не настроена. Попросите администратора выполнить `/setrole`.",
                ephemeral=True,
            )
            return
        role = interaction.guild.get_role(role_id)
        if role is None:
            await interaction.response.send_message(
                "Роль верификации не найдена. Попросите администратора проверить `/setrole`.",
                ephemeral=True,
            )
            return
        if role in interaction.user.roles:
            await interaction.response.send_message(
                "Вы уже верифицированы!", ephemeral=True
            )
            return
        try:
            await interaction.user.add_roles(role, reason="Верификация")
        except discord.Forbidden:
            await interaction.response.send_message(
                "У бота нет прав выдавать эту роль. Проверьте, что роль бота выше роли верификации.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                "Не удалось выдать роль. Попробуйте ещё раз.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "Вы верифицированы! Добро пожаловать на сервер.", ephemeral=True
        )


@bot.event
async def on_member_join(member: discord.Member):
    cfg = load_config()

    if NEW_ACCOUNT_DAYS > 0:
        age_days = (discord.utils.utcnow() - member.created_at).days
        if age_days < NEW_ACCOUNT_DAYS:
            try:
                await member.send(
                    f"Ваш аккаунт создан {age_days} дн. назад. На сервере запрещены аккаунты младше "
                    f"{int(NEW_ACCOUNT_DAYS)} дн. Вход отклонён."
                )
            except discord.Forbidden:
                pass
            await member.kick(reason="Слишком новый аккаунт (анти-рейд)")
            return

    channel_id = cfg.get("verify_channel_id")
    if channel_id:
        channel = member.guild.get_channel(channel_id)
        if channel:
            try:
                await member.send(
                    f"Добро пожаловать на сервер **{member.guild.name}**!\n"
                    f"Пройдите верификацию, нажав кнопку в канале {channel.mention}."
                )
            except discord.Forbidden:
                pass


@bot.event
async def on_ready():
    print(f"Бот {bot.user} готов (id={bot.user.id})")
    bot.add_view(VerifyView())
    try:
        synced = await bot.tree.sync()
        print(f"Синхронизировано слэш-команд: {len(synced)}")
    except Exception as exc:
        print(f"Ошибка синхронизации команд: {exc}")


@bot.tree.command(name="setrole", description="Указать роль, которая выдаётся после верификации (только для админов)")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def setrole(interaction: discord.Interaction, role: discord.Role):
    cfg = load_config()
    cfg["verify_role_id"] = role.id
    save_config(cfg)
    await interaction.response.send_message(
        f"Роль верификации: {role.mention}", ephemeral=True
    )


@bot.tree.command(name="setchannel", description="Указать канал для сообщения верификации (только для админов)")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def setchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    cfg = load_config()
    cfg["verify_channel_id"] = channel.id
    save_config(cfg)
    await interaction.response.send_message(
        f"Канал верификации: {channel.mention}", ephemeral=True
    )


@bot.tree.command(name="setup", description="Опубликовать сообщение верификации с кнопкой (только для админов)")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def setup(interaction: discord.Interaction, channel: discord.TextChannel = None):
    cfg = load_config()
    target = channel
    if target is None:
        target_id = cfg.get("verify_channel_id")
        target = interaction.guild.get_channel(target_id) if target_id else None
    if target is None:
        target = interaction.channel

    embed = discord.Embed(
        title="Верификация",
        description="Нажмите кнопку ниже, чтобы получить доступ к серверу.",
        color=BLURPLE,
    )
    try:
        await target.send(embed=embed, view=VerifyView())
    except discord.Forbidden:
        await interaction.response.send_message(
            "У бота нет прав писать в этот канал.", ephemeral=True
        )
        return
    await interaction.response.send_message(
        f"Сообщение верификации опубликовано в {target.mention}", ephemeral=True
    )


@setrole.error
@setchannel.error
@setup.error
async def admin_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "Эта команда доступна только администраторам сервера.", ephemeral=True
        )


async def main():
    web_app = web.Application()
    web_app.router.add_get("/", handle_root)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEB_PORT)
    await site.start()
    print(f"Web-сервер запущен на порту {WEB_PORT}")

    asyncio.create_task(keep_awake())

    await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("Ошибка: не задан DISCORD_TOKEN. Скопируйте .env.example в .env и укажите токен.")
        raise SystemExit(1)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
