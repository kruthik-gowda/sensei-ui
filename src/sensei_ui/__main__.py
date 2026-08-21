"""CLI entry point: `sensei-ui`."""
import webbrowser

import uvicorn

from sensei_ui.app import create_app

HOST = "127.0.0.1"
PORT = 8765


def main() -> None:
    webbrowser.open("http://%s:%d" % (HOST, PORT))
    uvicorn.run(create_app(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
