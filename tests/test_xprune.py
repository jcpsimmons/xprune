import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from xprune.api import oauth1_header
from xprune.core import classify, delete_pending, to_row
from xprune.store import Store


def post(pid, created, refs=None, text="hello"):
    p = {"id": pid, "author_id": "42", "created_at": created, "text": text,
         "public_metrics": {"impression_count": 10, "like_count": 1, "retweet_count": 0,
                            "reply_count": 0, "quote_count": 0, "bookmark_count": 0}}
    if refs:
        p["referenced_tweets"] = refs
    return p


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
        self.assertEqual(classify(post("1", "x")), ("original", None))
        self.assertEqual(classify(post("1", "x", [{"type": "replied_to", "id": "9"}])), ("reply", "9"))
        self.assertEqual(classify(post("1", "x", [{"type": "quoted", "id": "8"}])), ("quote", "8"))
        self.assertEqual(classify(post("1", "x", [{"type": "retweeted", "id": "7"}])), ("repost", "7"))

    def test_long_post_text_wins(self):
        p = post("1", "2025-01-01T00:00:00.000Z", text="short")
        p["note_tweet"] = {"text": "the full long version"}
        self.assertEqual(to_row(p, {}, {}, {})[4], "the full long version")


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.dir.name) / "t.db"))

    def tearDown(self):
        self.dir.cleanup()

    def test_upsert_never_revives_a_deleted_row(self):
        row = to_row(post("1", "2025-01-01T00:00:00.000Z"), {}, {}, {})
        self.store.upsert([row])
        self.store.mark("1", "deleted")
        self.store.upsert([row])
        self.assertEqual(self.store.pending("42"), [])
        self.assertEqual(self.store.summary("42"), [("original", "deleted", 1)])

    def test_pending_is_oldest_first_and_newest_works(self):
        self.store.upsert([to_row(post("2", "2025-02-01T00:00:00.000Z"), {}, {}, {}),
                           to_row(post("1", "2025-01-01T00:00:00.000Z"), {}, {}, {})])
        self.assertEqual([r[0] for r in self.store.pending("42")], ["1", "2"])
        self.assertEqual(self.store.newest("42").isoformat(), "2025-02-01T00:00:00+00:00")


class DeleteTest(unittest.TestCase):
    def test_routes_reposts_to_unretweet(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(str(Path(d) / "t.db"))
            store.upsert([
                to_row(post("1", "2025-01-01T00:00:00.000Z"), {}, {}, {}),
                to_row(post("2", "2025-01-02T00:00:00.000Z", [{"type": "retweeted", "id": "99"}]), {}, {}, {}),
            ])
            client = mock.Mock()
            client.log = lambda m: None

            def fake(method, path):
                if path == "/tweets/1":
                    return 200, {}, {"data": {"deleted": True}}
                if path == "/users/42/retweets/99":
                    return 200, {}, {"data": {"retweeted": False}}
                raise AssertionError(path)

            client.request.side_effect = fake
            counts = delete_pending(client, store, "42")
            self.assertEqual(counts["deleted"], 2)
            self.assertEqual(store.pending("42"), [])

    def test_auth_error_leaves_row_pending(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(str(Path(d) / "t.db"))
            store.upsert([to_row(post("1", "2025-01-01T00:00:00.000Z"), {}, {}, {})])
            client = mock.Mock()
            client.log = lambda m: None
            client.request.return_value = (403, {}, {"detail": "forbidden"})
            counts = delete_pending(client, store, "42")
            self.assertTrue(counts["stopped_on_auth_error"])
            self.assertEqual([r[0] for r in store.pending("42")], ["1"])


if __name__ == "__main__":
    unittest.main()
