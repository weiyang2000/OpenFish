"""Compatibility launcher for the BettaFish FastAPI service."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    """Run the versioned SaaS API when users start the project with python app.py."""
    host = os.getenv("BETTAFISH_API_HOST", "0.0.0.0")
    port = int(os.getenv("BETTAFISH_API_PORT", "8000"))
    uvicorn.run("apps.api.main:create_app", host=host, port=port, factory=True)


if __name__ == "__main__":
    main()
