import unittest
from unittest import mock

from xprune.api import oauth1_header
from datetime import datetime, timezone

from xprune.core import Post, classify, delete_posts, find_posts


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


class FindTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("xprune.core.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def client(self, posts):
        """Fake search: returns the posts inside the requested window, newest first, like X."""
        client = mock.Mock()
        client.calls = []

        def get(path, params, app=False):
            client.calls.append((params["start_time"], params["end_time"]))
            hits = [p for p in posts if params["start_time"] <= p["created_at"][:19] + "Z" < params["end_time"]]
            return {"data": sorted(hits, key=lambda p: p["created_at"], reverse=True), "meta": {}}

        client.get.side_effect = get
        return client

    def test_limit_stops_at_oldest_chunk(self):
        posts = [{"id": "1", "created_at": "2020-01-05T00:00:00.000Z"},
                 {"id": "2", "created_at": "2020-01-10T00:00:00.000Z",
                  "referenced_tweets": [{"type": "retweeted", "id": "9"}]},
                 {"id": "3", "created_at": "2024-06-01T00:00:00.000Z"}]
        client = self.client(posts)
        start, end = datetime(2020, 1, 1, tzinfo=timezone.utc), datetime(2025, 1, 1, tzinfo=timezone.utc)
        self.assertEqual([p.id for p in find_posts(client, "me", start, end, limit=1)], ["1"])
        self.assertEqual(len(client.calls), 1)
        found = find_posts(client, "me", start, end, types={"repost"}, limit=5)
        self.assertEqual([(p.id, p.ref_id) for p in found], [("2", "9")])

    def test_no_limit_is_one_window(self):
        posts = [{"id": "3", "created_at": "2024-06-01T00:00:00.000Z"},
                 {"id": "1", "created_at": "2020-01-05T00:00:00.000Z"}]
        client = self.client(posts)
        start, end = datetime(2020, 1, 1, tzinfo=timezone.utc), datetime(2025, 1, 1, tzinfo=timezone.utc)
        self.assertEqual([p.id for p in find_posts(client, "me", start, end)], ["1", "3"])
        self.assertEqual(len(client.calls), 1)


if __name__ == "__main__":
    unittest.main()
