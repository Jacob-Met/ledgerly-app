import unittest
from decimal import Decimal
from ledgerly.extract import RulesExtractor, HybridExtractor


class ExplicitNonchargeableFeeTests(unittest.TestCase):
    def extract(self, description):
        return RulesExtractor().extract(
            "From: Nia Santos <nia@example.test>\n"
            "Please invoice 2 hours at GBP 75 per hour for editing, "
            f"plus a separate GBP 25 {description} fee."
        )

    def test_nonchargeable_descriptions_require_review(self):
        for description in (
            "waived proofreading", "complimentary proofreading", "free proofreading",
            "already included proofreading", "non-billable proofreading",
            "proofreading waiver", "non-chargeable proofreading",
            "no-charge transcription", "at no charge transcription",
            "not chargeable transcription", "not-billable transcription",
            "pro bono transcription", "pro-bono transcription",
            "without charge transcription", "no cost transcription",
            "NO CHARGE transcription", "no additional charge transcription",
            "no extra cost transcription", "not charged transcription",
            "not payable transcription", "gratis transcription",
            "not to be charged transcription", "not be charged transcription",
            "without any charge transcription", "without a charge transcription",
            "not to be billed transcription", "not to be invoiced transcription",
            "do not charge transcription", "not to invoice transcription",
        ):
            with self.subTest(description=description):
                result = self.extract(description)
                self.assertTrue(result.errors)
                class UnneededModel:
                    def extract(self, text):
                        raise AssertionError("A model cannot authorize no-charge work")
                for body in (
                    "Please invoice 2 hours at GBP 75 per hour for editing, "
                    f"plus a separate GBP 25 {description} fee.",
                    f"- 2 hours editing @ GBP 75\n- 1 x {description} @ GBP 25",
                    "| Description | Qty | Price |\n| Editing | 2 | GBP 75 |\n"
                    f"| {description} | 1 | GBP 25 |",
                ):
                    source = "From: Nia Santos <nia@example.test>\n" + body + "\nNet 30"
                    checked = HybridExtractor(RulesExtractor(), UnneededModel()).extract(source)
                    self.assertTrue(checked.errors)
                    self.assertTrue(any(
                        issue.severity == "error" and "optional, conditional or excluded" in issue.message
                        for issue in checked.issues))

    def test_original_compound_request_separates_the_blocked_fee_for_review(self):
        result = self.extract("no-charge transcription")
        self.assertEqual(
            [(item.desc, item.qty, item.unit_price) for item in result.line_items],
            [("Editing", Decimal("2"), Decimal("75")),
             ("No-charge transcription fee", Decimal("1"), Decimal("25"))],
        )
        self.assertTrue(result.errors)
        self.assertTrue(any(issue.field == "line_items[1].unit_price"
                            and "excluded" in issue.message for issue in result.errors))
        self.assertFalse(any(issue.field.startswith("line_items[0].")
                             for issue in result.errors))
        self.assertFalse(any("Multiple monetary amounts" in issue.message
                             for issue in result.issues))

    def test_explicit_charge_descriptions_remain_supported(self):
        for description in (
            "one-off transcription", "fixed transcription", "cancellation",
            "discounted transcription", "no-code implementation", "no-show cancellation",
            "freeform illustration",
        ):
            with self.subTest(description=description):
                result = self.extract(description)
                self.assertFalse(result.errors)
                self.assertEqual(result.total(), Decimal("175"))
                self.assertEqual(len(result.line_items), 2)


if __name__ == "__main__":
    unittest.main()
