#!/usr/bin/env python3
"""
casterbase Discord bot  —  RestoreCord / DoubleCounter ID Searcher
"""

import os
import sys
import csv
import io
import mmap
import time
import logging
import discord
from pathlib import Path
from discord.ext import commands
from discord import app_commands

logging.basicConfig(
    level=logging.INFO,
    format="[casterbase] %(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout
)
log = logging.getLogger("casterbase")
log.info("starting up...")

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
_HERE                = Path(__file__).resolve().parent
RESTORECORD_FOLDER   = str(_HERE / "RestoreCordDatabase")
DOUBLECOUNTER_FOLDER = str(_HERE / "DoubleCounterDatabase")
SCAN_EXTENSIONS      = {".txt", ".csv", ".log", ".tsv"}
MAX_RESULTS          = 25   # max matches shown across all embeds

token = os.environ.get("BOT_TOKEN")
if not token:
    log.error("BOT_TOKEN not set — exiting")
    sys.exit(1)

log.info(f"token loaded ({len(token)} chars)")
log.info(f"RC  folder: {RESTORECORD_FOLDER}  exists={Path(RESTORECORD_FOLDER).exists()}")
log.info(f"DC  folder: {DOUBLECOUNTER_FOLDER}  exists={Path(DOUBLECOUNTER_FOLDER).exists()}")

# ─────────────────────────────────────────────
#  FILE DISCOVERY
# ─────────────────────────────────────────────
def find_files(folder: str) -> list[Path]:
    base = Path(folder).resolve()
    if not base.is_dir():
        return []
    return sorted(
        p for p in base.iterdir()
        if p.is_file() and p.suffix.lower() in SCAN_EXTENSIONS
    )

# ─────────────────────────────────────────────
#  MMAP SEARCH
# ─────────────────────────────────────────────
def mmap_contains_any(path: Path, needles: list[bytes]) -> list[bytes]:
    try:
        if path.stat().st_size == 0:
            return []
        with open(path, "rb") as fh:
            with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                return [n for n in needles if mm.find(n) != -1]
    except (ValueError, OSError):
        try:
            raw = path.read_bytes()
            return [n for n in needles if n in raw]
        except OSError:
            return []

# ─────────────────────────────────────────────
#  DETAIL EXTRACTION
# ─────────────────────────────────────────────
ENCODINGS = ["utf-8", "utf-8-sig", "latin-1", "cp1252"]

def read_text(path: Path) -> tuple[list[str], str]:
    for enc in ENCODINGS:
        try:
            return path.read_text(encoding=enc).splitlines(), enc
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace").splitlines(), "utf-8(lossy)"

def extract_matches(path: Path, target_ids: list[str]) -> list[dict]:
    try:
        lines, encoding = read_text(path)
    except OSError:
        return []

    matches: list[dict] = []
    ext = path.suffix.lower()

    if ext in {".csv", ".tsv"}:
        delimiter = "\t" if ext == ".tsv" else ","
        try:
            reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter=delimiter)
            if reader.fieldnames:
                for row_num, row in enumerate(reader, start=2):
                    row_text = " ".join(str(v) for v in row.values())
                    for tid in target_ids:
                        if tid in row_text:
                            matches.append({
                                "id":     tid,
                                "file":   path.name,
                                "line":   row_num,
                                "format": "CSV",
                                "data":   {k: v for k, v in row.items() if v},
                            })
                if matches:
                    return matches
        except Exception:
            pass

    for line_num, line in enumerate(lines, start=1):
        for tid in target_ids:
            if tid in line:
                matches.append({
                    "id":     tid,
                    "file":   path.name,
                    "line":   line_num,
                    "format": "TEXT",
                    "data":   line.strip(),
                })
    return matches

def search_folder(folder: str, target_ids: list[str]) -> list[dict]:
    needles = [tid.encode() for tid in target_ids]
    files   = find_files(folder)
    log.info(f"searching {len(files)} files for {target_ids}")
    all_matches = []
    for path in files:
        hit_needles = mmap_contains_any(path, needles)
        if not hit_needles:
            continue
        hit_ids = [target_ids[needles.index(n)] for n in hit_needles]
        all_matches.extend(extract_matches(path, hit_ids))
    return all_matches

# ─────────────────────────────────────────────
#  PARSE QUERIES  (IDs or usernames)
# ─────────────────────────────────────────────
def parse_queries(raw: str) -> list[str]:
    seen = set()
    out  = []
    for part in raw.split(","):
        q = part.strip()
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out

# ─────────────────────────────────────────────
#  FORMAT ONE MATCH ROW
# ─────────────────────────────────────────────
def format_match(m: dict) -> str:
    data = m.get("data")
    if isinstance(data, dict):
        return "\n".join(f"`{k}` — {v}" for k, v in data.items() if v) or "*(empty)*"
    return f"```{str(data)[:300]}```"

# ─────────────────────────────────────────────
#  BUILD EMBEDS
#  — splits into multiple embeds if > 25 fields
# ─────────────────────────────────────────────
def build_embeds(matches: list[dict], queries: list[str],
                 db_label: str, elapsed: float) -> list[discord.Embed]:
    from collections import defaultdict
    grouped: dict[str, list[dict]] = defaultdict(list)
    for m in matches:
        grouped[m["id"]].append(m)

    color  = 0x9B59B6
    embeds = []

    if not matches:
        em = discord.Embed(
            title=f"🔍  {db_label}  —  No results",
            description="\n".join(f"`{q}` — not found" for q in queries),
            color=color
        )
        em.set_footer(text=f"⏱  {elapsed:.2f}s")
        return [em]

    for query in queries:
        group = grouped.get(query, [])
        total = len(group)

        if not group:
            em = discord.Embed(
                title=f"🔍  {db_label}  —  `{query}`",
                description="Not found.",
                color=color
            )
            em.set_footer(text=f"⏱  {elapsed:.2f}s")
            embeds.append(em)
            continue

        # chunk into embeds of 10 matches each (Discord limit: 25 fields)
        chunk_size = 10
        chunks     = [group[i:i+chunk_size] for i in range(0, min(total, MAX_RESULTS), chunk_size)]

        for page, chunk in enumerate(chunks, 1):
            page_label = f"  —  page {page}/{len(chunks)}" if len(chunks) > 1 else ""
            em = discord.Embed(
                title=f"✅  {db_label}  —  `{query}`{page_label}",
                description=f"**{total}** match{'es' if total != 1 else ''} found"
                            + (f"  *(showing {MAX_RESULTS} max)*" if total > MAX_RESULTS else ""),
                color=color
            )
            for i, m in enumerate(chunk, start=(page-1)*chunk_size + 1):
                em.add_field(
                    name=f"#{i}  •  {m['file']}  •  line {m['line']}",
                    value=format_match(m),
                    inline=False
                )
            em.set_footer(text=f"⏱  {elapsed:.2f}s  •  {chunk[-1]['format']}")
            embeds.append(em)

    return embeds

# ─────────────────────────────────────────────
#  SHARED SEARCH HANDLER
# ─────────────────────────────────────────────
async def handle_search(interaction: discord.Interaction,
                        query: str, folder: str, db_label: str):
    await interaction.response.defer(thinking=True)
    queries = parse_queries(query)
    if not queries:
        await interaction.followup.send("❌  Nothing entered.")
        return
    log.info(f"{db_label} search from {interaction.user} — {queries}")
    start   = time.perf_counter()
    matches = search_folder(folder, queries)
    elapsed = time.perf_counter() - start
    log.info(f"done — {len(matches)} matches in {elapsed:.2f}s")
    embeds  = build_embeds(matches, queries, db_label, elapsed)
    # Discord allows max 10 embeds per followup, send in batches
    for i in range(0, len(embeds), 10):
        batch = embeds[i:i+10]
        if i == 0:
            await interaction.followup.send(embeds=batch)
        else:
            await interaction.followup.send(embeds=batch)

# ─────────────────────────────────────────────
#  BOT + SLASH COMMANDS
# ─────────────────────────────────────────────
intents = discord.Intents.default()
bot     = commands.Bot(command_prefix="!", intents=intents)

@bot.tree.command(name="restorecord", description="Search RestoreCord by user ID or username")
@app_commands.describe(query="ID, username, or multiple separated by commas: 123456 , SomeUser")
async def cmd_restorecord(interaction: discord.Interaction, query: str):
    await handle_search(interaction, query, RESTORECORD_FOLDER, "RestoreCord")

@bot.tree.command(name="doublecounter", description="Search DoubleCounter by user ID or username")
@app_commands.describe(query="ID, username, or multiple separated by commas: 123456 , SomeUser")
async def cmd_doublecounter(interaction: discord.Interaction, query: str):
    await handle_search(interaction, query, DOUBLECOUNTER_FOLDER, "DoubleCounter")

@bot.tree.command(name="ping", description="Check if the bot is alive")
async def cmd_ping(interaction: discord.Interaction):
    log.info(f"ping from {interaction.user}")
    await interaction.response.send_message(f"🟢  Online  •  `{round(bot.latency * 1000)}ms`")

# ─────────────────────────────────────────────
#  BADGE MAPPING
# ─────────────────────────────────────────────
BADGE_FLAGS = {
    discord.PublicUserFlags.staff:                    "Discord Staff",
    discord.PublicUserFlags.partner:                  "Partnered Server Owner",
    discord.PublicUserFlags.hypesquad:                "HypeSquad Events",
    discord.PublicUserFlags.bug_hunter:               "Bug Hunter Lvl 1",
    discord.PublicUserFlags.bug_hunter_level_2:       "Bug Hunter Lvl 2",
    discord.PublicUserFlags.hypesquad_bravery:        "HypeSquad Bravery",
    discord.PublicUserFlags.hypesquad_brilliance:     "HypeSquad Brilliance",
    discord.PublicUserFlags.hypesquad_balance:        "HypeSquad Balance",
    discord.PublicUserFlags.early_supporter:          "Early Supporter",
    discord.PublicUserFlags.verified_bot_developer:   "Verified Bot Dev",
    discord.PublicUserFlags.active_developer:         "Active Developer",
    discord.PublicUserFlags.discord_certified_moderator: "Certified Moderator",
}

def get_badges(user: discord.User) -> str:
    flags  = user.public_flags
    badges = [label for flag, label in BADGE_FLAGS.items() if getattr(flags, flag.name, False)]
    return ", ".join(badges) if badges else "None"

def snowflake_age(user: discord.User) -> tuple[str, str]:
    """Returns (reg_date, account_age) as strings."""
    created = user.created_at
    now     = discord.utils.utcnow()
    delta   = now - created

    years  = delta.days // 365
    months = (delta.days % 365) // 30
    days   = delta.days % 30

    reg_date = created.strftime("%d/%m/%Y")

    parts = []
    if years:
        parts.append(f"{years}y")
    if months:
        parts.append(f"{months}mo")
    if days or not parts:
        parts.append(f"{days}d")
    age_str = " ".join(parts)

    return reg_date, age_str

# ─────────────────────────────────────────────
#  PROFILE COMMAND
# ─────────────────────────────────────────────
@bot.tree.command(name="profile", description="Look up a Discord user's profile by ID")
@app_commands.describe(user_id="The Discord user ID to look up")
async def cmd_profile(interaction: discord.Interaction, user_id: str):
    await interaction.response.defer(thinking=True)
    log.info(f"profile lookup from {interaction.user} — target: {user_id}")

    user_id = user_id.strip()
    if not user_id.isdigit():
        await interaction.followup.send("❌  Please provide a valid numeric Discord user ID.")
        return

    try:
        user = await bot.fetch_user(int(user_id))
    except discord.NotFound:
        await interaction.followup.send(f"❌  No Discord account found for ID `{user_id}`.")
        return
    except discord.HTTPException as e:
        await interaction.followup.send(f"❌  Discord API error: `{e}`")
        return

    reg_date, acc_age = snowflake_age(user)
    badges            = get_badges(user)
    avatar_url        = user.display_avatar.url
    banner_url        = user.banner.url if user.banner else None
    display_name      = user.display_name
    username          = str(user)          # user#discriminator or new @username
    is_bot            = "✅ Yes" if user.bot else "No"
    nitro_avatar      = "✅ Yes" if user.display_avatar.is_animated() else "No"

    em = discord.Embed(
        title="Profile Search",
        color=user.accent_color or 0x000000
    )

    em.add_field(
        name="| Registration Date |",
        value=f"`{reg_date}`",
        inline=True
    )
    em.add_field(
        name="| Account Age |",
        value=f"`{acc_age}`",
        inline=True
    )
    em.add_field(
        name="| Badges |",
        value=f"`{badges}`",
        inline=False
    )
    em.add_field(
        name="| Username |",
        value=f"`{username}`",
        inline=True
    )
    em.add_field(
        name="| Display Name |",
        value=f"`{display_name}`",
        inline=True
    )
    em.add_field(
        name="| User ID |",
        value=f"`{user.id}`",
        inline=True
    )
    em.add_field(
        name="| Bot Account |",
        value=is_bot,
        inline=True
    )
    em.add_field(
        name="| Animated Avatar |",
        value=nitro_avatar,
        inline=True
    )
    em.add_field(
        name="| Avatar URL |",
        value=f"[Click here]({avatar_url})",
        inline=False
    )

    em.set_thumbnail(url=avatar_url)

    if banner_url:
        em.set_image(url=banner_url)

    em.set_footer(text=f"Requested by {interaction.user}  •  ID: {user.id}")

    await interaction.followup.send(embed=em)
    log.info(f"profile sent for {user} ({user.id})")

# ─────────────────────────────────────────────
#  HELP EMBED  (shared by !help and /help)
# ─────────────────────────────────────────────
def help_embed() -> discord.Embed:
    em = discord.Embed(
        title="casterbase  —  Command List",
        color=0x9B59B6
    )
    em.add_field(
        name="!rc  `<user id>`  —  RestoreCord",
        value="Search the RestoreCord database.\n"
              "Enable Developer Mode in Discord settings, right-click a user → Copy ID, then paste it.\n"
              "Example: `!rc 123456789012345678`",
        inline=False
    )
    em.add_field(
        name="!dc  `<user id>`  —  DoubleCounter",
        value="Search the DoubleCounter database.\n"
              "Enable Developer Mode in Discord settings, right-click a user → Copy ID, then paste it.\n"
              "Example: `!dc 123456789012345678`",
        inline=False
    )
    em.add_field(
        name="!profile  `<user id>`",
        value="Look up a Discord user's public profile — avatar, banner, badges, registration date, account age.\n"
              "Enable Developer Mode in Discord settings, right-click a user → Copy ID, then paste it.\n"
              "Example: `!profile 123456789012345678`",
        inline=False
    )
    em.add_field(
        name="!ping",
        value="Check the bot's latency.\n"
              "Example: `!ping`",
        inline=False
    )
    em.set_footer(text="Slash command equivalents: /restorecord  /doublecounter  /profile  /ping")
    return em

# ─────────────────────────────────────────────
#  PREFIX COMMANDS  (!rc  !dc  !profile  !ping  !help)
# ─────────────────────────────────────────────
@bot.command(name="rc")
async def prefix_rc(ctx: commands.Context, *, query: str = ""):
    if not query:
        await ctx.send("❌  Usage: `!rc <user id or username>`")
        return
    log.info(f"!rc from {ctx.author} — {query}")
    queries = parse_queries(query)
    async with ctx.typing():
        start   = time.perf_counter()
        matches = search_folder(RESTORECORD_FOLDER, queries)
        elapsed = time.perf_counter() - start
    embeds = build_embeds(matches, queries, "RestoreCord", elapsed)
    for i in range(0, len(embeds), 10):
        await ctx.send(embeds=embeds[i:i+10])

@bot.command(name="dc")
async def prefix_dc(ctx: commands.Context, *, query: str = ""):
    if not query:
        await ctx.send("❌  Usage: `!dc <user id or username>`")
        return
    log.info(f"!dc from {ctx.author} — {query}")
    queries = parse_queries(query)
    async with ctx.typing():
        start   = time.perf_counter()
        matches = search_folder(DOUBLECOUNTER_FOLDER, queries)
        elapsed = time.perf_counter() - start
    embeds = build_embeds(matches, queries, "DoubleCounter", elapsed)
    for i in range(0, len(embeds), 10):
        await ctx.send(embeds=embeds[i:i+10])

@bot.command(name="profile")
async def prefix_profile(ctx: commands.Context, user_id: str = ""):
    if not user_id or not user_id.strip().isdigit():
        await ctx.send("❌  Usage: `!profile <user id>`")
        return
    log.info(f"!profile from {ctx.author} — {user_id}")
    async with ctx.typing():
        try:
            user = await bot.fetch_user(int(user_id))
        except discord.NotFound:
            await ctx.send(f"❌  No Discord account found for ID `{user_id}`.")
            return
        except discord.HTTPException as e:
            await ctx.send(f"❌  Discord API error: `{e}`")
            return

        reg_date, acc_age = snowflake_age(user)
        badges            = get_badges(user)
        avatar_url        = user.display_avatar.url
        banner_url        = user.banner.url if user.banner else None
        display_name      = user.display_name
        username          = str(user)
        is_bot            = "✅ Yes" if user.bot else "No"
        nitro_avatar      = "✅ Yes" if user.display_avatar.is_animated() else "No"

        em = discord.Embed(title="Profile Search", color=user.accent_color or 0x000000)
        em.add_field(name="| Registration Date |", value=f"`{reg_date}`",    inline=True)
        em.add_field(name="| Account Age |",       value=f"`{acc_age}`",     inline=True)
        em.add_field(name="| Badges |",            value=f"`{badges}`",      inline=False)
        em.add_field(name="| Username |",          value=f"`{username}`",    inline=True)
        em.add_field(name="| Display Name |",      value=f"`{display_name}`",inline=True)
        em.add_field(name="| User ID |",           value=f"`{user.id}`",     inline=True)
        em.add_field(name="| Bot Account |",       value=is_bot,             inline=True)
        em.add_field(name="| Animated Avatar |",   value=nitro_avatar,       inline=True)
        em.add_field(name="| Avatar URL |",        value=f"[Click here]({avatar_url})", inline=False)
        em.set_thumbnail(url=avatar_url)
        if banner_url:
            em.set_image(url=banner_url)
        em.set_footer(text=f"Requested by {ctx.author}  •  ID: {user.id}")

    await ctx.send(embed=em)

@bot.command(name="ping")
async def prefix_ping(ctx: commands.Context):
    log.info(f"!ping from {ctx.author}")
    await ctx.send(f"🟢  Online  •  `{round(bot.latency * 1000)}ms`")

@bot.command(name="help", aliases=["h"])
async def prefix_help(ctx: commands.Context):
    log.info(f"!help from {ctx.author}")
    await ctx.send(embed=help_embed())

# slash /help
@bot.tree.command(name="help", description="Show all available commands")
async def cmd_help(interaction: discord.Interaction):
    log.info(f"/help from {interaction.user}")
    await interaction.response.send_message(embed=help_embed())

# ─────────────────────────────────────────────
#  STARTUP
# ─────────────────────────────────────────────
@bot.event
async def on_ready():
    log.info(f"logged in as {bot.user}")
    synced = await bot.tree.sync()
    log.info(f"synced {len(synced)} commands: {[c.name for c in synced]}")

log.info("connecting to Discord...")
bot.run(token, log_handler=None)
