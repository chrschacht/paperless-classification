import unittest

from app.services.ocr_text_quality import assess_pdf_text_quality


class PdfTextQualityTest(unittest.TestCase):
    def test_accepts_normal_paperless_ocr_text(self):
        text = """
        Bayerische Landesärztekammer
        Beitragsbescheid 2026
        Der Jahresbeitrag beträgt 1.181,00 EUR.
        Bitte überweisen Sie den Betrag innerhalb eines Monats unter Angabe
        Ihrer Mitgliedsnummer auf das unten genannte Konto.
        """
        accepted, reason = assess_pdf_text_quality(text, page_count=1)
        self.assertTrue(accepted, reason)

    def test_rejects_llm_reasoning_instead_of_ocr(self):
        text = "<think>" + ("Wait no, I need to inspect the address again. " * 100) + "</think>"
        accepted, _ = assess_pdf_text_quality(text, page_count=1)
        self.assertFalse(accepted)

    def test_rejects_repetition_loop_without_think_tags(self):
        text = ("The address might be different and I need to inspect it again.\n" * 30)
        accepted, _ = assess_pdf_text_quality(text, page_count=1)
        self.assertFalse(accepted)

    def test_rejects_tiny_text_layer_on_multipage_pdf(self):
        accepted, _ = assess_pdf_text_quality("Scan received 25.08.2026", page_count=8)
        self.assertFalse(accepted)


if __name__ == "__main__":
    unittest.main()
