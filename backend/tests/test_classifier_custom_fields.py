import unittest
from types import SimpleNamespace

from app.services.classifier.service import (
    DocumentClassifierService,
    _custom_field_value_is_valid,
    _omit_empty_custom_fields,
)
from app.services.classifier.base_provider import ClassificationResult
from app.services.classifier.tool_executor import effective_custom_field_document_types


class OmitEmptyCustomFieldsTest(unittest.TestCase):
    def test_omits_null_and_blank_values(self):
        self.assertEqual(
            _omit_empty_custom_fields({
                "Mahnstufe": None,
                "Zahldatum": "  ",
                "Betrag": 26.82,
                "Rechnungsnummer": "AR41178",
                "Bezahlt": False,
            }),
            {
                "Betrag": 26.82,
                "Rechnungsnummer": "AR41178",
                "Bezahlt": False,
            },
        )


    def test_keeps_zero_and_false_values(self):
        self.assertEqual(
            _omit_empty_custom_fields({"Betrag": 0, "Bezahlt": False}),
            {"Betrag": 0, "Bezahlt": False},
        )


class EffectiveCustomFieldDocumentTypesTest(unittest.TestCase):
    def test_tax_number_is_never_applicable_to_incoming_invoices(self):
        self.assertEqual(
            effective_custom_field_document_types(
                "Steuernummer", ["Bescheid", "Eingangsrechnung", "Information"],
            ),
            ["Bescheid", "Information"],
        )

    def test_other_invoice_fields_keep_their_configured_types(self):
        self.assertEqual(
            effective_custom_field_document_types(
                "Zahldatum", ["Eingangsrechnung", "Ausgangsrechnung"],
            ),
            ["Eingangsrechnung", "Ausgangsrechnung"],
        )


class RequiredCustomFieldValueTest(unittest.TestCase):
    def mapping(self, **overrides):
        values = {
            "paperless_field_name": "Rechnungsnummer",
            "ignore_values": "",
            "validation_regex": "",
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_blank_and_ignored_values_are_missing(self):
        mapping = self.mapping(ignore_values="unbekannt, n/a")
        self.assertFalse(_custom_field_value_is_valid(mapping, "  "))
        self.assertFalse(_custom_field_value_is_valid(mapping, "N/A"))

    def test_zero_and_false_are_real_values(self):
        mapping = self.mapping()
        self.assertTrue(_custom_field_value_is_valid(mapping, 0))
        self.assertTrue(_custom_field_value_is_valid(mapping, False))

    def test_regex_and_select_options_are_validated(self):
        mapping = self.mapping(validation_regex=r"RE-\d+")
        self.assertTrue(_custom_field_value_is_valid(mapping, "RE-123"))
        self.assertFalse(_custom_field_value_is_valid(mapping, "123"))
        select_mapping = self.mapping()
        definition = {
            "data_type": "select",
            "extra_data": {"select_options": [{"id": "7", "label": "Offen"}]},
        }
        self.assertTrue(_custom_field_value_is_valid(select_mapping, "Offen", definition))
        self.assertFalse(_custom_field_value_is_valid(select_mapping, "Erledigt", definition))

    def test_non_latin_script_letters_are_rejected(self):
        mapping = self.mapping()
        self.assertFalse(_custom_field_value_is_valid(mapping, "AZ-2026-测试"))
        self.assertFalse(_custom_field_value_is_valid(mapping, "Vertrag-Договор"))

    def test_latin_letters_digits_and_reference_punctuation_are_allowed(self):
        mapping = self.mapping()
        self.assertTrue(_custom_field_value_is_valid(mapping, "MÜ-2026/123-A"))
        self.assertTrue(_custom_field_value_is_valid(mapping, "EUR6817.00"))


class IncomingInvoicePaymentMethodTest(unittest.TestCase):
    def setUp(self):
        self.service = DocumentClassifierService(None, None)

    def test_credit_without_other_payment_information_defaults_to_transfer(self):
        result = ClassificationResult(
            document_type="Eingangsrechnung",
            custom_fields={"Zahlungsart": None},
        )

        self.service._enforce_incoming_invoice_fields(
            result,
            "Rechnungskorrektur. Zurückerstattet. Zahlbetrag -15,99 EUR",
        )

        self.assertEqual(result.custom_fields["Zahlungsart"], "Überweisung")

    def test_explicit_card_information_is_not_overwritten(self):
        result = ClassificationResult(
            document_type="Eingangsrechnung",
            custom_fields={"Zahlungsart": None},
        )

        self.service._enforce_incoming_invoice_fields(
            result,
            "Gutschrift über 15,99 EUR auf die verwendete Kreditkarte.",
        )

        self.assertIsNone(result.custom_fields["Zahlungsart"])

    def test_bank_not_present_in_document_is_removed(self):
        result = ClassificationResult(
            document_type="Eingangsrechnung",
            custom_fields={"Bank": "apoBank", "Zahlungsart": None},
        )

        self.service._enforce_incoming_invoice_fields(
            result,
            "Amazon Rechnungskorrektur. Zurückerstattet. Zahlbetrag -15,99 EUR",
        )

        self.assertIsNone(result.custom_fields["Bank"])
        self.assertEqual(result.custom_fields["Zahlungsart"], "Überweisung")

    def test_unsupported_guessed_method_on_credit_is_replaced(self):
        result = ClassificationResult(
            document_type="Eingangsrechnung",
            custom_fields={"Zahlungsart": "Lastschrift"},
        )

        self.service._enforce_incoming_invoice_fields(
            result,
            "Rechnungskorrektur. Zurückerstattet. Zahlbetrag -15,99 EUR",
        )

        self.assertEqual(result.custom_fields["Zahlungsart"], "Überweisung")

if __name__ == "__main__":
    unittest.main()
