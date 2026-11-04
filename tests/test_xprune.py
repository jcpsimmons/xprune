import unittest
from unittest import mock

from xprune.api import oauth1_header
from xprune.core import Post, classify, delete_posts


class OAuthTest(unittest.TestCase):
    def test_signature_matches_published_example(self):
        # Worked example from X's "Creating a signature" documentation.
        url = ("https://api.twitter.com/1.1/statuses/update.json?include_entities=true"
               "&status=Hello%20Ladies%20%2B%20Gentlemen%2C%20a%20signed%20OAuth%20request%21")
        header = oauth1_header(
            "POST", url,
            consumer_key="xvz1evFS4wEEPTGEFPHBog",
            consumer_secret="kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
            token="370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb",
            token_secret="LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE",
            nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg",
            timestamp=1318622958,
        )
        self.assertIn('oauth_signature="hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D"', header)


class ClassifyTest(unittest.TestCase):
    def test_types(self):
        self.assertEqual(classify({}), ("original", None))
        self.assertEqual(classify({"referenced_tweets": [{"type": "replied_to", "id": "9"}]}), ("reply", "9"))
        self.assertEqual(classify({"referenced_tweets": [{"type": "quoted", "id": "8"}]}), ("quote", "8"))
        self.assertEqual(classify({"referenced_tweets": [{"type": "retweeted", "id": "7"}]}), ("repost", "7"))


def fake_client(handler):
    client = mock.Mock()
    client.log = lambda m: None
    client.request.side_effect = handler
    return client


class DeleteTest(unittest.TestCase):
    def test_routes_reposts_to_unretweet(self):
        calls = []

        def handler(method, path):
            calls.append((method, path))
            if path == "/tweets/1":
                return 200, {}, {"data": {"deleted": True}}
            if path == "/users/42/retweets/99":
                return 200, {}, {"data": {"retweeted": False}}
            return 404, {}, {"detail": "not found"}

        posts = [Post("1", "2025-01-01T00:00:00.000Z", "original", None),
                 Post("2", "2025-01-02T00:00:00.000Z", "repost", "99"),
                 Post("3", "2025-01-03T00:00:00.000Z", "reply", "5")]
        counts = delete_posts(fake_client(handler), "42", posts)
        self.assertEqual((counts["deleted"], counts["not_found"]), (2, 1))
        self.assertIn(("DELETE", "/users/42/retweets/99"), calls)

    def test_auth_error_stops(self):
        posts = [Post(str(i), f"2025-01-0{i}T00:00:00.000Z", "original", None) for i in range(1, 4)]
        client = fake_client(lambda method, path: (403, {}, {"detail": "forbidden"}))
        counts = delete_posts(client, "42", posts)
        self.assertTrue(counts["stopped_on_auth_error"])
        self.assertEqual(client.request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
