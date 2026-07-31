"""
Daily Income, Fuel & Expense Tracker Bot
Telegram Bot — python-telegram-bot v20+
"""

import json
import os
import re
import logging
from datetime import datetime, timedelta
from pathlib import Path
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Persistence ──────────────────────────────────────────────────────────────
DATA_FILE = "data.json"

def load_data() -> dict:
    if Path(DATA_FILE).exists():
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_data(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

# Global state — keyed by str(user_id)
user_states: dict = load_data()

def get_user(user_id: int) -> dict:
    key = str(user_id)
    if key not in user_states:
        user_states[key] = {
            "trips": [],
            "expenses": [],
            "status": "WORKING",
            "history": []
        }
        save_data(user_states)
    return user_states[key]

def save_user(user_id: int):
    save_data(user_states)

# ─── Formatting & Calculations ────────────────────────────────────────────────
def fmt(amount: float) -> str:
    """Format as Indonesian Rupiah: Rp1.234.567"""
    return "Rp" + f"{int(round(amount)):,}".replace(",", ".")

def parse_amount(text: str) -> float | None:
    """Parse '50k', '50000', '50.000' → 50000.0"""
    t = text.strip().lower().replace(".", "").replace(",", "")
    try:
        if t.endswith("k"):
            return float(t[:-1]) * 1000
        return float(t)
    except ValueError:
        return None

def gross_commission(omzet: float) -> float:
    if omzet < 550_000:
        return omzet * 0.40
    elif omzet < 1_000_000:
        return 220_000 + (omzet - 550_000) * 0.50
    else:
        return omzet * 0.50

def tier_name(omzet: float) -> str:
    if omzet < 550_000:
        return "Tier 1 (40%)"
    elif omzet < 1_000_000:
        return "Tier 2 — Progressive (40% → 50%)"
    else:
        return "🏆 Tier 3 / JACKPOT (50% Flat)"

def target_tracker(omzet: float) -> str:
    if omzet < 550_000:
        return f"🎯 Target Tier 2 (50%): Sisa *{fmt(550_000 - omzet)}* lagi!"
    elif omzet < 1_000_000:
        return f"🔥 TIER 2 AKTIF\\! Kejar Jackpot Rp1 Juta: Sisa *{fmt(1_000_000 - omzet)}* lagi\\!"
    else:
        return "🎉 *JACKPOT TIER 3 (50% TOTAL)\\!* Omzet lu tembus Rp1 Juta\\!"

def fuel_cost(km: float) -> float:
    return (km / 11) * 10_000

def fuel_liters(km: float) -> float:
    return km / 11

# ─── Handlers ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    await update.message.reply_text(
        "👋 *Halo, bro\\!* Selamat datang di *Income Tracker Bot*\\!\n\n"
        "📌 *Cara Pakai:*\n"
        "• Kirim nominal trip: `35000` atau `35k`\n"
        "• Catat pengeluaran: `parkir 5000` atau `exp makan 15000`\n"
        "• Cek status: `cek`\n"
        "• Undo terakhir: `batal`\n"
        "• Tutup shift: `selesai`\n"
        "• Rekap: `rekap minggu ini` / `rekap bulan ini`\n\n"
        "Yuk mulai shift\\! 🚀",
        parse_mode="MarkdownV2"
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *PANDUAN LENGKAP*\n\n"
        "*Input trip:* `40000` atau `40k`\n"
        "*Pengeluaran:* `parkir 5000` atau `exp tambal ban 20000`\n"
        "*Cek shift:* `cek` atau `status`\n"
        "*Undo:* `batal` atau `undo`\n"
        "*Tutup shift:* `selesai`\n"
        "*Rekap:* `rekap minggu ini` / `rekap bulan ini`\n\n"
        "*Formula:*\n"
        "• ⛽ Bensin = (KM ÷ 11) × Rp10\\.000\n"
        "• 💰 Tier 1 (<550k): Omzet × 40%\n"
        "• 💰 Tier 2 (550k–999k): Rp220k + sisa × 50%\n"
        "• 💰 Tier 3 (≥1jt): Omzet × 50%\n"
        "• 🎯 Net = Komisi − Bensin − Pengeluaran",
        parse_mode="MarkdownV2"
    )

async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin shortcut to manually reset session (trips + expenses only)."""
    uid = update.effective_user.id
    user = get_user(uid)
    user["trips"] = []
    user["expenses"] = []
    user["status"] = "WORKING"
    save_user(uid)
    await update.message.reply_text("🔄 Sesi hari ini di-reset\\.  History tetap aman\\!", parse_mode="MarkdownV2")


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = get_user(uid)
    text = update.message.text.strip()
    text_lower = text.lower()

    # ── AWAITING_KM branch ────────────────────────────────────────────────────
    if user["status"] == "AWAITING_KM":
        km = parse_amount(text)
        if km is None or km <= 0:
            await update.message.reply_text(
                "⚠️ Harap masukkan angka Total KM yang valid (contoh: `110` atau `85\\.5`)\\.",
                parse_mode="MarkdownV2"
            )
            return
        await close_shift(update, uid, user, km)
        return

    # ── WORKING branch ────────────────────────────────────────────────────────

    # --- rekap ---
    if "rekap minggu ini" in text_lower:
        await do_rekap(update, uid, user, "week")
        return
    if "rekap bulan ini" in text_lower:
        await do_rekap(update, uid, user, "month")
        return

    # --- selesai ---
    if text_lower in ("selesai", "shift selesai"):
        if not user["trips"]:
            await update.message.reply_text(
                "⚠️ Lu belum memasukkan trip sama sekali hari ini\\!",
                parse_mode="MarkdownV2"
            )
            return
        omzet = sum(user["trips"])
        total_exp = sum(e["amount"] for e in user["expenses"])
        n = len(user["trips"])
        user["status"] = "AWAITING_KM"
        save_user(uid)
        await update.message.reply_text(
            f"🏁 *AKHIR SHIFT*\n\n"
            f"Total Trip: *{n} Trip*\n"
            f"Total Omzet Kotor: *{fmt(omzet)}*\n"
            f"Total Pengeluaran Lain: *{fmt(total_exp)}*\n\n"
            f"Berapa *Total KM* tempuh lu hari ini\\?\n"
            f"_(Kirim angkanya saja, misal: `110`)_",
            parse_mode="MarkdownV2"
        )
        return

    # --- cek / status ---
    if text_lower in ("cek", "status"):
        await do_cek(update, user)
        return

    # --- batal / undo ---
    if text_lower in ("batal", "undo"):
        await do_batal(update, uid, user)
        return

    # --- expense: "parkir 5000" or "exp parkir 5000" ---
    expense_match = parse_expense(text)
    if expense_match:
        desc, amount = expense_match
        user["expenses"].append({"desc": desc, "amount": amount})
        save_user(uid)
        total_exp = sum(e["amount"] for e in user["expenses"])
        await update.message.reply_text(
            f"💸 *Pengeluaran Dicatat\\!*\n"
            f"• Detail: {escape_md(desc)} \\({fmt(amount)}\\)\n"
            f"• Total Pengeluaran Lain: *{fmt(total_exp)}*",
            parse_mode="MarkdownV2"
        )
        return

    # --- numeric trip ---
    amount = parse_amount(text)
    if amount and amount > 0:
        user["trips"].append(amount)
        save_user(uid)
        n = len(user["trips"])
        omzet = sum(user["trips"])
        tracker = target_tracker(omzet)
        await update.message.reply_text(
            f"✅ *Trip \\#{n} Dicatat\\!*\n"
            f"• Nominal: *{fmt(amount)}*\n"
            f"• Total Omzet \\({n} Trip\\): *{fmt(omzet)}*\n\n"
            f"{tracker}",
            parse_mode="MarkdownV2"
        )
        return

    # --- unknown ---
    await update.message.reply_text(
        "🤔 Perintah tidak dikenali\\. Ketik /help untuk panduan\\.",
        parse_mode="MarkdownV2"
    )


# ─── Sub-handlers ─────────────────────────────────────────────────────────────

def parse_expense(text: str):
    """Return (desc, amount) or None."""
    # "exp <desc> <amount>" or "<desc> <amount>"
    t = text.strip()
    if t.lower().startswith("exp "):
        t = t[4:].strip()
    # Last token is amount, rest is description
    parts = t.rsplit(None, 1)
    if len(parts) == 2:
        desc, amount_str = parts
        amount = parse_amount(amount_str)
        if amount and amount > 0 and not desc.replace(".", "").replace(",", "").isdigit():
            return desc.capitalize(), amount
    return None


def escape_md(text: str) -> str:
    """Escape special MarkdownV2 characters."""
    special = r'\_*[]()~`>#+-=|{}.!'
    return re.sub(f"([{re.escape(special)}])", r'\\\1', text)


async def do_cek(update: Update, user: dict):
    if not user["trips"] and not user["expenses"]:
        await update.message.reply_text(
            "📄 Belum ada trip atau pengeluaran yang dicatat hari ini\\.",
            parse_mode="MarkdownV2"
        )
        return

    omzet = sum(user["trips"])
    total_exp = sum(e["amount"] for e in user["expenses"])

    trip_lines = "\n".join(
        f"  • Trip \\#{i+1}: {fmt(t)}" for i, t in enumerate(user["trips"])
    ) or "  _\\(Belum ada\\)_"

    exp_lines = "\n".join(
        f"  • {escape_md(e['desc'])}: {fmt(e['amount'])}" for e in user["expenses"]
    ) or "  _\\(Belum ada\\)_"

    tracker = target_tracker(omzet)

    await update.message.reply_text(
        f"📊 *RINCIAN SHIFT SEMENTARA*\n\n"
        f"*Trip:*\n{trip_lines}\n"
        f"*Total Omzet:* {fmt(omzet)}\n\n"
        f"*Pengeluaran Lain:*\n{exp_lines}\n"
        f"*Total Pengeluaran:* {fmt(total_exp)}\n\n"
        f"\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\n"
        f"{tracker}\n\n"
        f"Ketik *selesai* jika mau tutup shift\\.",
        parse_mode="MarkdownV2"
    )


async def do_batal(update: Update, uid: int, user: dict):
    if user["trips"]:
        removed = user["trips"].pop()
        save_user(uid)
        await update.message.reply_text(
            f"↩️ Trip terakhir *{fmt(removed)}* dihapus\\.\n"
            f"Total Omzet sekarang: *{fmt(sum(user['trips']))}*",
            parse_mode="MarkdownV2"
        )
    elif user["expenses"]:
        removed = user["expenses"].pop()
        save_user(uid)
        await update.message.reply_text(
            f"↩️ Pengeluaran *{escape_md(removed['desc'])}* \\({fmt(removed['amount'])}\\) dihapus\\.",
            parse_mode="MarkdownV2"
        )
    else:
        await update.message.reply_text(
            "⚠️ Tidak ada data yang bisa di-undo\\.",
            parse_mode="MarkdownV2"
        )


async def close_shift(update: Update, uid: int, user: dict, km: float):
    omzet = sum(user["trips"])
    total_exp = sum(e["amount"] for e in user["expenses"])
    fuel = fuel_cost(km)
    liters = fuel_liters(km)
    commission = gross_commission(omzet)
    net = commission - fuel - total_exp
    n = len(user["trips"])
    today = datetime.now().strftime("%Y-%m-%d")

    trip_lines = "\n".join(
        f"  {i+1}\\. Trip {i+1}: {fmt(t)}" for i, t in enumerate(user["trips"])
    )

    exp_lines = "\n".join(
        f"  • {escape_md(e['desc'])}: {fmt(e['amount'])}" for e in user["expenses"]
    ) or "  _\\(Tidak ada\\)_"

    user["history"].append({
        "date": today,
        "omzet": omzet,
        "km": km,
        "fuelCost": round(fuel),
        "extraExpenses": round(total_exp),
        "netIncome": round(net)
    })
    user["trips"] = []
    user["expenses"] = []
    user["status"] = "WORKING"
    save_user(uid)

    await update.message.reply_text(
        f"📝 *LAPORAN PENDAPATAN HARIAN*\n"
        f"\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\n"
        f"📍 *Rincian Trip \\({n} Trip\\):*\n{trip_lines}\n"
        f"\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\n"
        f"🛵 *Total Jarak:* {escape_md(f'{km:.1f}')} KM \\({escape_md(f'{liters:.2f}')} Liter Pertalite\\)\n"
        f"💵 *Total Omzet Kotor:* {fmt(omzet)}\n"
        f"📊 *Skema/Tier:* {escape_md(tier_name(omzet))}\n"
        f"\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\n"
        f"💰 *Komisi Kotor:* {fmt(commission)}\n"
        f"⛽ *Biaya Bensin:* \\-{fmt(fuel)}\n"
        f"💸 *Pengeluaran Lain:* \\-{fmt(total_exp)}\n"
        f"{exp_lines}\n"
        f"\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\n"
        f"🎯 *TAKE\\-HOME PAY \\(NET\\):* *{fmt(net)}*\n\n"
        f"✅ *Shift Berhasil Ditutup\\!* Data disimpan & di\\-reset untuk besok 👍",
        parse_mode="MarkdownV2"
    )


async def do_rekap(update: Update, uid: int, user: dict, period: str):
    now = datetime.now()
    if period == "week":
        start = now - timedelta(days=now.weekday())
        label = "MINGGU INI"
    else:
        start = now.replace(day=1)
        label = "BULAN INI"
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)

    records = [
        h for h in user["history"]
        if datetime.strptime(h["date"], "%Y-%m-%d") >= start
    ]

    if not records:
        await update.message.reply_text(
            f"📭 Belum ada data untuk *{escape_md(label)}*\\.",
            parse_mode="MarkdownV2"
        )
        return

    days = len(records)
    total_omzet = sum(r["omzet"] for r in records)
    total_km = sum(r["km"] for r in records)
    total_fuel = sum(r["fuelCost"] for r in records)
    total_exp = sum(r["extraExpenses"] for r in records)
    total_net = sum(r["netIncome"] for r in records)
    avg_net = total_net / days if days else 0

    await update.message.reply_text(
        f"📅 *REKAP PENDAPATAN {escape_md(label)}*\n"
        f"\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\n"
        f"🗓️ Total Shift Kerja: *{days} Hari*\n"
        f"🛵 Total Jarak: *{escape_md(f'{total_km:.1f}')} KM*\n"
        f"💵 Total Omzet Kotor: *{fmt(total_omzet)}*\n"
        f"\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\n"
        f"⛽ Total Biaya Bensin: \\-*{fmt(total_fuel)}*\n"
        f"💸 Total Pengeluaran Lain: \\-*{fmt(total_exp)}*\n"
        f"\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\n"
        f"🎯 *TOTAL TAKE\\-HOME PAY \\(NET\\):* *{fmt(total_net)}*\n"
        f"💡 *Rata\\-rata Bersih/Hari:* *{fmt(avg_net)}*",
        parse_mode="MarkdownV2"
    )


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN environment variable not set!")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
