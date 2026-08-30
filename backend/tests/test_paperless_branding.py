import asyncio
import unittest

from app.routers.paperless import (
    get_paperless_branding,
    get_paperless_branding_logo,
)
from app.services.paperless_client import (
    PAPERLESS_DEFAULT_THEME_COLOR,
    _extract_paperless_theme_color,
    _resolve_paperless_asset_url,
)


class PaperlessBrandingAssetUrlTest(unittest.TestCase):
    def test_resolves_logo_below_plain_base_url(self):
        self.assertEqual(
            _resolve_paperless_asset_url(
                "https://paperless.example.test",
                "/logo/brand.png",
            ),
            "https://paperless.example.test/logo/brand.png",
        )

    def test_preserves_paperless_path_prefix(self):
        self.assertEqual(
            _resolve_paperless_asset_url(
                "https://example.test/paperless",
                "/logo/brand.png",
            ),
            "https://example.test/paperless/logo/brand.png",
        )

    def test_accepts_absolute_url_on_same_origin(self):
        self.assertEqual(
            _resolve_paperless_asset_url(
                "https://paperless.example.test",
                "https://paperless.example.test/logo/brand.png",
            ),
            "https://paperless.example.test/logo/brand.png",
        )

    def test_rejects_absolute_url_on_different_origin(self):
        with self.assertRaises(ValueError):
            _resolve_paperless_asset_url(
                "https://paperless.example.test",
                "https://untrusted.example.test/logo.svg",
            )


class PaperlessThemeColorTest(unittest.TestCase):
    def test_reads_nested_user_theme_color(self):
        self.assertEqual(
            _extract_paperless_theme_color({"theme": {"color": "#9FBF2F"}}),
            "#9fbf2f",
        )

    def test_rejects_css_injection_and_uses_paperless_default(self):
        self.assertEqual(
            _extract_paperless_theme_color({"theme": {"color": "red; display:none"}}),
            PAPERLESS_DEFAULT_THEME_COLOR,
        )


class PaperlessBrandingRouterTest(unittest.TestCase):
    class FakeClient:
        base_url = "https://paperless.example.test"

        async def get_application_branding(self):
            return {
                "title": "Mein Archiv",
                "logo_path": "/logo/brand.png",
                "design_color": "#9fbf2f",
            }

        async def get_application_logo(self, logo_path):
            if logo_path != "/logo/brand.png":
                raise AssertionError("Unexpected logo path")
            return b"fake-png", "image/png"

    def test_returns_title_and_local_logo_proxy(self):
        result = asyncio.run(get_paperless_branding(self.FakeClient()))
        self.assertEqual(result["title"], "Mein Archiv")
        self.assertEqual(result["design_color"], "#9fbf2f")
        self.assertRegex(
            result["logo_url"],
            r"^/api/paperless/branding/logo\?v=[0-9a-f]{12}$",
        )

    def test_logo_proxy_keeps_image_type_and_security_headers(self):
        response = asyncio.run(get_paperless_branding_logo(self.FakeClient()))
        self.assertEqual(response.body, b"fake-png")
        self.assertEqual(response.media_type, "image/png")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertIn("sandbox", response.headers["content-security-policy"])


if __name__ == "__main__":
    unittest.main()
