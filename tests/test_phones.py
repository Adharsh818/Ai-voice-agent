"""Indian phone numbers: canonical form, read-back and masking."""

import unittest

import phones


class PhoneTests(unittest.TestCase):
    def test_accepted_forms_normalise_to_e164(self):
        for raw in ["9876543210", "98765 43210", "+91 98765 43210", "919876543210", "09876543210",
                    "+91-98765-43210", "(+91) 98765 43210"]:
            self.assertEqual(phones.to_e164(raw), "+919876543210", raw)
        self.assertEqual(phones.to_e164("080 2345 6789"), "+918023456789")     # Bengaluru landline
        self.assertEqual(phones.to_e164("+91 22 1234 5678"), "+912212345678")  # landline written without its 0

    def test_rejected_forms(self):
        for raw in ["", None, "12345", "98765432101", "+1 415 555 0100", "00000000000",
                    "+91 0987654321", "0987654321"]:
            self.assertIsNone(phones.to_e164(raw), raw)

    def test_any_ten_digits_is_a_number(self):
        # Owner, 25 Oct: "a valid number is exactly 10 digits". "(258) 369-1470" (a misheard 9, most
        # likely) was refused and Emma kept asking for more; the read-back catches a misheard digit.
        self.assertEqual(phones.to_e164("(258) 369-1470"), "+912583691470")
        self.assertEqual(phones.spoken("+912583691470"), "2 5 8 3 6, 9 1 4 7 0")      # as said, no 0 added

    def test_read_back_is_digit_by_digit_in_two_groups(self):
        self.assertEqual(phones.spoken("+919876543210"), "9 8 7 6 5, 4 3 2 1 0")
        # Landlines: STD code, then the number in two (not eight digits in one breath).
        self.assertEqual(phones.spoken("+914424567890", landline=True), "0 4 4, 2 4 5 6, 7 8 9 0")  # Chennai
        self.assertEqual(phones.spoken("+914222345678", landline=True), "0 4 2 2, 2 3 4, 5 6 7 8")  # Coimbatore
        # +91 80...: a Bengaluru landline or a mobile starting 80; how it was said decides.
        self.assertEqual(phones.spoken("+918023456789"), "8 0 2 3 4, 5 6 7 8 9")
        self.assertEqual(phones.spoken("+918023456789", landline=True), "0 8 0, 2 3 4 5, 6 7 8 9")
        self.assertTrue(phones.said_as_landline("080 2345 6789"))
        self.assertFalse(phones.said_as_landline("098765 43210"))                      # a mobile with its 0
        self.assertFalse(phones.said_as_landline("80234 56789"))
        self.assertEqual(phones.masked("+919876543210"), "******3210")
        self.assertTrue(phones.is_mobile("+919876543210"))


if __name__ == "__main__":
    unittest.main()
