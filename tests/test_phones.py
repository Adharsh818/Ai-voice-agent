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
        for raw in ["", None, "12345", "5876543210", "98765432101", "+1 415 555 0100", "00000000000",
                    "+91 0987654321"]:
            self.assertIsNone(phones.to_e164(raw), raw)

    def test_read_back_is_digit_by_digit_in_two_groups(self):
        self.assertEqual(phones.spoken("+919876543210"), "9 8 7 6 5, 4 3 2 1 0")
        self.assertEqual(phones.masked("+919876543210"), "******3210")
        self.assertTrue(phones.is_mobile("+919876543210"))


if __name__ == "__main__":
    unittest.main()
