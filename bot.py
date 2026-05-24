from threading import Thread

from telegram_bot import run_telegram_bot
from web_app import run_web_server


def main() -> None:
    web_thread = Thread(target=run_web_server, daemon=True)
    web_thread.start()
    run_telegram_bot()


if __name__ == "__main__":
    main()


