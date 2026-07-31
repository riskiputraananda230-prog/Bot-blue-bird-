import json
import os
import re
import logging
from datetime import datetime, timedelta
from pathlib import Path
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_FILE = "data.json"

def load_data() -> dict:
    if Path(DATA_FILE).exists():
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_data(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

user_states: dict = load_data()

def get_user(user_id: int) -> dict:
    key = str(user_id)
    if key not in user_states:
        user_states[key] = {"trips": [], "expenses": [], "status": "WORKING", "history": []}
        save_data(user_states)
    return user_states[key]

def save_user(user_id: int):
    save_data(user_states)

def fmt(amount: float) -> str:
    return "Rp" + f"{int(round(amount)):,}".replace(",", ".")

def h(text: str) -> str:
    """Escape HTML special characters."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def parse_amount(text: str):
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
        return "Tier 3 / JACKPOT (50% Flat)"

def target_tracker(omzet: float) -> str:
    if omzet < 550_000:
        sisa = fmt(550_000 - omzet)
        return f"🎯 Target Tier 2 (50%): Sisa <b>{h(sisa)}</b> lagi!"
    elif omzet < 1_000_000:
        sisa = fmt(1_000_000 - omzet)
        return f"🔥 TIER 2 AKTIF! Kejar Jackpot Rp1 Juta: Sisa <b>{h(sisa)}</b> lagi!"
    else:
        return "🎉 <b>JACKPOT TIER 3 (50% TOTAL)!</b> Omzet lu tembus Rp1 Juta!"

def fuel_cost(km: float) -> float:
    return (km / 11) * 10_000

def fuel_liters(km: float) -> float:
    return km / 11

def parse_expense(text: str):
    t = text.strip()
    if t.lower().startswith("exp "):
        t = t[4:].strip()
    parts = t.rsplit(None, 1)
    if len(parts) == 2:
        desc, amount_str = parts
        amount = parse_amount(amount_str)
        if amount and amount > 0 and not desc.replace(".", "").replace(",", "").isdigit():
            return desc.capitalize(), amount
    return None

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    get_user(update.effective_user.id)
    await update.message.reply_text(
        "👋 <b>Halo, bro!</b> Selamat datang di <b>Income Tracker Bot</b>!\n\n"
        "📌 <b>Cara Pakai:</b>\n"
        "• Kirim nominal trip: <code>35000</code> atau <code>35k</code>\n"
        "• Catat pengeluaran: <code>parkir 5000</code> atau <code>exp makan 15000</code>\n"
        "• Cek status: <code>cek</code>\n"
        "• Undo terakhir: <code>batal</code>\n"
        "• Tutup shift: <code>selesai</code>\n"
        "• Rekap: <code>rekap minggu ini</code> / <code>rekap bulan ini</code>\n\n"
        "Yuk mulai shift! 🚀",
        parse_mode="HTML"
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>PANDUAN LENGKAP</b>\n\n"
        "<b>Input trip:</b> <code>40000</code> atau <code>40k</code>\n"
        "<b>Pengeluaran:</b> <code>parkir 5000</code> atau <code>exp tambal ban 20000</code>\n"
        "<b>Cek shift:</b> <code>cek</code> atau <code>status</code>\n"
        "<b>Undo:</b> <code>batal</code> atau <code>undo</code>\n"
        "<b>Tutup shift:</b> <code>selesai</code>\n"
        "<b>Rekap:</b> <code>rekap minggu ini</code> / <code>rekap bulan ini</code>\n\n"
        "<b>Formula:</b>\n"
        "• ⛽ Bensin = (KM ÷ 11) × Rp10.000\n"
        "• 💰 Tier 1 (&lt;550k): Omzet × 40%\n"
        "• 💰 Tier 2 (550k–999k): Rp220k + sisa × 50%\n"
        "• 💰 Tier 3 (≥1jt): Omzet × 50%\n"
        "• 🎯 Net = Komisi − Bensin − Pengeluaran",
        parse_mode="HTML"
    )

async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = get_user(uid)
    user["trips"] = []
    user["expenses"] = []
    user["status"] = "WORKING"
    save_user(uid)
    await update.message.reply_text("🔄 Sesi hari ini di-reset. History tetap aman!", parse_mode="HTML")

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = get_user(uid)
    text = update.message.text.strip()
    text_lower = text.lower()

    if user["status"] == "AWAITING_KM":
        km = parse_amount(text)
        if km is None or km <= 0:
            await update.message.reply_text(
                "⚠️ Harap masukkan angka Total KM yang valid (contoh: <code>110</code> atau <code>85.5</code>).",
                parse_mode="HTML"
            )
            return
        await close_shift(update, uid, user, km)
        return

    if "rekap minggu ini" in text_lower:
        await do_rekap(update, uid, user, "week")
        return
    if "rekap bulan ini" in text_lower:
        await do_rekap(update, uid, user, "month")
        return

    if text_lower in ("selesai", "shift selesai"):
        if not user["trips"]:
            await update.message.reply_text("⚠️ Lu belum memasukkan trip sama sekali hari ini!", parse_mode="HTML")
            return
        omzet = sum(user["trips"])
        total_exp = sum(e["amount"] for e in user["expenses"])
        n = len(user["trips"])
        user["status"] = "AWAITING_KM"
        save_user(uid)
        await update.message.reply_text(
            f"🏁 <b>AKHIR SHIFT</b>\n\n"
            f"Total Trip: <b>{n} Trip</b>\n"
            f"Total Omzet Kotor: <b>{h(fmt(omzet))}</b>\n"
            f"Total Pengeluaran Lain: <b>{h(fmt(total_exp))}</b>\n\n"
            f"Berapa <b>Total KM</b> tempuh lu hari ini?\n"
            f"<i>(Kirim angkanya saja, misal: <code>110</code>)</i>",
            parse_mode="HTML"
        )
        return

    if text_lower in ("cek", "status"):
        await do_cek(update, user)
        return

    if text_lower in ("batal", "undo"):
        await do_batal(update, uid, user)
        return

    expense_match = parse_expense(text)
    if expense_match:
        desc, amount = expense_match
        user["expenses"].append({"desc": desc, "amount": amount})
        save_user(uid)
        total_exp = sum(e["amount"] for e in user["expenses"])
        await update.message.reply_text(
            f"💸 <b>Pengeluaran Dicatat!</b>\n"
            f"• Detail: {h(desc)} ({h(fmt(amount))})\n"
            f"• Total Pengeluaran Lain: <b>{h(fmt(total_exp))}</b>",
            parse_mode="HTML"
        )
        return

    amount = parse_amount(text)
    if amount and amount > 0:
        user["trips"].append(amount)
        save_user(uid)
        n = len(user["trips"])
        omzet = sum(user["trips"])
        tracker = target_tracker(omzet)
        await update.message.reply_text(
            f"✅ <b>Trip #{n} Dicatat!</b>\n"
            f"• Nominal: <b>{h(fmt(amount))}</b>\n"
            f"• Total Omzet ({n} Trip): <b>{h(fmt(omzet))}</b>\n\n"
            f"{tracker}",
            parse_mode="HTML"
        )
        return

    await update.message.reply_text("🤔 Perintah tidak dikenali. Ketik /help untuk panduan.", parse_mode="HTML")

async def do_cek(update: Update, user: dict):
    if not user["trips"] and not user["expenses"]:
        await update.message.reply_text("📄 Belum ada trip atau pengeluaran yang dicatat hari ini.", parse_mode="HTML")
        return
    omzet = sum(user["trips"])
    total_exp = sum(e["amount"] for e in user["expenses"])
    trip_lines = "\n".join(f"  • Trip #{i+1}: {h(fmt(t))}" for i, t in enumerate(user["trips"])) or "  <i>(Belum ada)</i>"
    exp_lines = "\n".join(f"  • {h(e['desc'])}: {h(fmt(e['amount']))}" for e in user["expenses"]) or "  <i>(Belum ada)</i>"
    tracker = target_tracker(omzet)
    await update.message.reply_text(
        f"📊 <b>RINCIAN SHIFT SEMENTARA</b>\n\n"
        f"<b>Trip:</b>\n{trip_lines}\n"
        f"<b>Total Omzet:</b> {h(fmt(omzet))}\n\n"
        f"<b>Pengeluaran Lain:</b>\n{exp_lines}\n"
        f"<b>Total Pengeluaran:</b> {h(fmt(total_exp))}\n\n"
        f"———————————————\n"
        f"{tracker}\n\n"
        f"Ketik <b>selesai</b> jika mau tutup shift.",
        parse_mode="HTML"
    )

async def do_batal(update: Update, uid: int, user: dict):
    if user["trips"]:
        removed = user["trips"].pop()
        save_user(uid)
        await update.message.reply_text(
            f"↩️ Trip terakhir <b>{h(fmt(removed))}</b> dihapus.\n"
            f"Total Omzet sekarang: <b>{h(fmt(sum(user['trips'])))}</b>",
            parse_mode="HTML"
        )
    elif user["expenses"]:
        removed = user["expenses"].pop()
        save_user(uid)
        await update.message.reply_text(
            f"↩️ Pengeluaran <b>{h(removed['desc'])}</b> ({h(fmt(removed['amount']))}) dihapus.",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text("⚠️ Tidak ada data yang bisa di-undo.", parse_mode="HTML")

async def close_shift(update: Update, uid: int, user: dict, km: float):
    omzet = sum(user["trips"])
    total_exp = sum(e["amount"] for e in user["expenses"])
    fuel = fuel_cost(km)
    liters = fuel_liters(km)
    commission = gross_commission(omzet)
    net = commission - fuel - total_exp
    n = len(user["trips"])
    today = datetime.now().strftime("%Y-%m-%d")
    trip_lines = "\n".join(f"  {i+1}. Trip {i+1}: {h(fmt(t))}" for i, t in enumerate(user["trips"]))
    exp_lines = "\n".join(f"  • {h(e['desc'])}: {h(fmt(e['amount']))}" for e in user["expenses"]) or "  <i>(Tidak ada)</i>"
    user["history"].append({
        "date": today, "omzet": omzet, "km": km,
        "fuelCost": round(fuel), "extraExpenses": round(total_exp), "netIncome": round(net)
    })
    user["trips"] = []
    user["expenses"] = []
    user["status"] = "WORKING"
    save_user(uid)
    await update.message.reply_text(
        f"📝 <b>LAPORAN PENDAPATAN HARIAN</b>\n"
        f"———————————————\n"
        f"📍 <b>Rincian Trip ({n} Trip):</b>\n{trip_lines}\n"
        f"———————————————\n"
        f"🛵 <b>Total Jarak:</b> {km:.1f} KM ({liters:.2f} Liter Pertalite)\n"
        f"💵 <b>Total Omzet Kotor:</b> {h(fmt(omzet))}\n"
        f"📊 <b>Skema/Tier:</b> {h(tier_name(omzet))}\n"
        f"———————————————\n"
        f"💰 <b>Komisi Kotor:</b> {h(fmt(commission))}\n"
        f"⛽ <b>Biaya Bensin:</b> -{h(fmt(fuel))}\n"
        f"💸 <b>Pengeluaran Lain:</b> -{h(fmt(total_exp))}\n"
        f"{exp_lines}\n"
        f"———————————————\n"
        f"🎯 <b>TAKE-HOME PAY (NET): {h(fmt(net))}</b>\n\n"
        f"✅ <b>Shift Berhasil Ditutup!</b> Data disimpan &amp; di-reset untuk besok 👍",
        parse_mode="HTML"
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
    records = [h for h in user["history"] if datetime.strptime(h["date"], "%Y-%m-%d") >= start]
    if not records:
        await update.message.reply_text(f"📭 Belum ada data untuk <b>{label}</b>.", parse_mode="HTML")
        return
    days = len(records)
    total_omzet = sum(r["omzet"] for r in records)
    total_km = sum(r["km"] for r in records)
    total_fuel = sum(r["fuelCost"] for r in records)
    total_exp = sum(r["extraExpenses"] for r in records)
    total_net = sum(r["netIncome"] for r in records)
    avg_net = total_net / days if days else 0
    await update.message.reply_text(
        f"📅 <b>REKAP PENDAPATAN {label}</b>\n"
        f"———————————————\n"
        f"🗓️ Total Shift Kerja: <b>{days} Hari</b>\n"
        f"🛵 Total Jarak: <b>{total_km:.1f} KM</b>\n"
        f"💵 Total Omzet Kotor: <b>{h(fmt(total_omzet))}</b>\n"
        f"———————————————\n"
        f"⛽ Total Biaya Bensin: -<b>{h(fmt(total_fuel))}</b>\n"
        f"💸 Total Pengeluaran Lain: -<b>{h(fmt(total_exp))}</b>\n"
        f"———————————————\n"
        f"🎯 <b>TOTAL TAKE-HOME PAY (NET): {h(fmt(total_net))}</b>\n"
        f"💡 <b>Rata-rata Bersih/Hari: {h(fmt(avg_net))}</b>",
        parse_mode="HTML"
    )

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
    
