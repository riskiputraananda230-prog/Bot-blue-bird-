# Income Tracker Telegram Bot

Bot Telegram untuk tracking income harian ojek online.

## Deploy ke Railway

1. Upload folder ini ke GitHub
2. Connect repo ke Railway.app
3. Tambah environment variable: `BOT_TOKEN=<token_bot_telegram>`
4. Deploy otomatis!

## Environment Variables

| Variable | Value |
|----------|-------|
| BOT_TOKEN | Token dari @BotFather |

## Commands

- `35000` atau `35k` → catat trip
- `parkir 5000` → catat pengeluaran  
- `cek` → status shift
- `batal` → undo terakhir
- `selesai` → tutup shift
- `rekap minggu ini` / `rekap bulan ini` → laporan
