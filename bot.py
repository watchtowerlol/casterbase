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

# force logs to show immediately
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
#  PARSE IDs
# ─────────────────────────────────────────────
def parse_ids(raw: str) -> list[str]:
    seen = set()
    ids  = []
    for part in raw.split(","):
        tid = part.strip()
        if tid and tid not in seen:
            seen.add(tid)
            ids.append(tid)
    return ids

# ─────────────────────────────────────────────
#  BUILD EMBED
# ─────────────────────────────────────────────
def build_embeds(matches: list[dict], target_ids: list[str],
                 db_label: str, elapsed: float) -> list[discord.Embed]:
    from collections import defaultdict
    grouped: dict[str, list[dict]] = defaultdict(list)
    for m in matches:
        grouped[m["id"]].append(m)

    color = 0x9B59B6

    if not matches:
        em = discord.Embed(
            title=f"🔍  {db_label}  —  No results",
            description="\n".join(f"`{tid}` — not found" for tid in target_ids),
            color=color
        )
        em.set_footer(text=f"⏱  {elapsed:.2f}s")
        return [em]

    embeds = []
    for tid in target_ids:
        group = grouped.get(tid, [])
        if not group:
            em = discord.Embed(
                title=f"🔍  {db_label}  —  `{tid}`",
                description="Not found.",
                color=color
            )
            em.set_footer(text=f"⏱  {elapsed:.2f}s")
            embeds.append(em)
            continue

        em = discord.Embed(
            title=f"✅  {db_label}  —  `{tid}`",
            description=f"**{len(group)}** match{'es' if len(group) != 1 else ''} found",
            color=color
        )
        for i, m in enumerate(group[:5], 1):
            data = m.get("data")
            if isinstance(data, dict):
                body = "\n".join(f"`{k}` — {v}" for k, v in data.items() if v)
            else:
                body = f"```{str(data)[:300]}```"
            em.add_field(
                name=f"Match #{i}  •  {m['file']}  •  line {m['line']}",
                value=body or "*(empty row)*",
                inline=False
            )
        if len(group) > 5:
            em.add_field(
                name="...",
                value=f"*{len(group) - 5} more matches not shown*",
                inline=False
            )
        em.set_footer(text=f"⏱  {elapsed:.2f}s  •  {m['format']}")
        embeds.append(em)

    return embeds

# ─────────────────────────────────────────────
#  BOT
# ─────────────────────────────────────────────
intents = discord.Intents.default()
bot     = commands.Bot(command_prefix="!", intents=intents)

@bot.tree.command(name="restorecord", description="Search RestoreCord database by user ID(s)")
@app_commands.describe(ids="One or more IDs separated by commas: 123456 , 789012")
async def cmd_restorecord(interaction: discord.Interaction, ids: str):
    log.info(f"restorecord command from {interaction.user} — ids: {ids}")
    await interaction.response.defer(thinking=True)
    target_ids = parse_ids(ids)
    if not target_ids:
        await interaction.followup.send("❌  No valid IDs provided.")
        return
    start   = time.perf_counter()
    matches = search_folder(RESTORECORD_FOLDER, target_ids)
    elapsed = time.perf_counter() - start
    log.info(f"restorecord done — {len(matches)} matches in {elapsed:.2f}s")
    embeds  = build_embeds(matches, target_ids, "RestoreCord", elapsed)
    for em in embeds:
        await interaction.followup.send(embed=em)

@bot.tree.command(name="doublecounter", description="Search DoubleCounter database by user ID(s)")
@app_commands.describe(ids="One or more IDs separated by commas: 123456 , 789012")
async def cmd_doublecounter(interaction: discord.Interaction, ids: str):
    log.info(f"doublecounter command from {interaction.user} — ids: {ids}")
    await interaction.response.defer(thinking=True)
    target_ids = parse_ids(ids)
    if not target_ids:
        await interaction.followup.send("❌  No valid IDs provided.")
        return
    start   = time.perf_counter()
    matches = search_folder(DOUBLECOUNTER_FOLDER, target_ids)
    elapsed = time.perf_counter() - start
    log.info(f"doublecounter done — {len(matches)} matches in {elapsed:.2f}s")
    embeds  = build_embeds(matches, target_ids, "DoubleCounter", elapsed)
    for em in embeds:
        await interaction.followup.send(embed=em)

@bot.tree.command(name="ping", description="Check if the bot is alive")
async def cmd_ping(interaction: discord.Interaction):
    log.info(f"ping from {interaction.user}")
    await interaction.response.send_message(f"🟢  Online  •  `{round(bot.latency * 1000)}ms`")

@bot.event
async def on_ready():
    log.info(f"logged in as {bot.user}")
    synced = await bot.tree.sync()
    log.info(f"synced {len(synced)} slash commands: {[c.name for c in synced]}")

log.info("connecting to Discord...")
bot.run(token, log_handler=None)
