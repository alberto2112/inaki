"""Wrapper para compatibilidad con `python main.py`. El código real está en inaki/cli.py."""

from inaki.cli import app

if __name__ == "__main__":
    app()
