from __future__ import annotations

from app.config import settings


def test_cors_origins_include_tailscale_frontend_variants():
    original_tailscale_host = settings.tailscale_host
    try:
        settings.tailscale_host = "sarah.tail1234.ts.net,100.101.218.34"

        origins = settings.cors_origins

        assert "http://localhost:3000" in origins
        assert "http://sarah.tail1234.ts.net:3000" in origins
        assert "https://sarah.tail1234.ts.net" in origins
        assert "http://100.101.218.34:3000" in origins
        assert "https://100.101.218.34" in origins
    finally:
        settings.tailscale_host = original_tailscale_host