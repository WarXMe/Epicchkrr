"""
main.py — Epic Games ULP Checker Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Run:  python main.py
Env:  TG_BOT_TOKEN=your_token  (or edit config.py directly)

Install deps:
  pip install python-telegram-bot requests
"""

from config import BOT_TOKEN, THREADS
from bot    import build_app


def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("[!] Set your bot token in config.py or via TG_BOT_TOKEN env var")
        return

    app = build_app()
    print(f"[*] Epic checker bot running | {THREADS} threads")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
