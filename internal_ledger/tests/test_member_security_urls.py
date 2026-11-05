import unittest

from internal_ledger.member_security import _same_origin_reset_url, _temporary_redirect


class ResetUrlTestCase(unittest.TestCase):
    def test_absolute_reset_url_stays_on_current_wireguard_origin(self):
        self.assertEqual(
            _same_origin_reset_url(
                "https://ledger.selfquanta.com/update-password?key=example"
            ),
            "/update-password?key=example",
        )

    def test_relative_reset_url_is_unchanged(self):
        self.assertEqual(
            _same_origin_reset_url("/update-password?key=example"),
            "/update-password?key=example",
        )

    def test_before_request_redirect_has_temporary_location_header(self):
        response = _temporary_redirect("/update-password?key=example").get_response()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/update-password?key=example")


if __name__ == "__main__":
    unittest.main()
