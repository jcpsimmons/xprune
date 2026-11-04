# xprune

Archive your old X posts to a database you own, then delete them from X through the official API.

Every post is written to SQLite or Postgres before anything is deleted: full text (including long posts), the raw API object, a snapshot of its metrics, and the post it replied to, quoted, or reposted when X still returns it. Deletes only ever touch rows that are already in the archive.

I built this to clear everything older than 90 days off my account on a schedule. It ran through about 4,700 posts in a day with no failures, and now runs daily.

## What it costs

X's API is pay-per-use. Prices from the [X pricing page](https://docs.x.com/x-api/getting-started/pricing), checked October 2026:

| Step | Endpoint | Price |
|---|---|---|
| Archive (read) | `GET /2/tweets/search/all` | $0.005 per post |
| Delete a post | `DELETE /2/tweets/:id` | about $0.010 per request |
| Undo a repost | `DELETE /2/users/:id/retweets/:id` | about $0.010 per request |
| Count | `GET /2/tweets/counts/all` | $0.010 per request (about 30 days per request) |

So 1,000 old posts cost about $15 to archive and delete. X may also bill the replied-to, quoted, and reposted posts that come back with each page, so treat that as a floor. The developer console shows what you were actually charged. `xprune count` gives you an estimate before you spend anything.

## How long it takes

X allows 50 deletes per 15 minutes on each delete endpoint. Posts and reposts use different endpoints with separate limits, so xprune runs them in parallel. Expect about 200 deletes an hour plus whatever share of your posts are reposts. The script sleeps through rate-limit windows on its own; leave it running.

## Setup

1. Create an app in the [X developer console](https://console.x.com) and buy some credits.
2. Under **User authentication settings**, set **App permissions** to **Read and write**.
3. On **Keys and tokens**, generate the API key and secret, the bearer token, and the access token and secret. If you change permissions later, **regenerate the access token**: a token made before the change keeps its old, read-only access, and X will answer every delete with 403. xprune checks this and stops before deleting.
4. Export the five values:

```sh
export X_CONSUMER_KEY=...
export X_CONSUMER_SECRET=...
export X_ACCESS_TOKEN=...
export X_ACCESS_TOKEN_SECRET=...
export X_BEARER_TOKEN=...
```

## Install

Python 3.10 or newer. No dependencies for SQLite.

```sh
pip install git+https://github.com/jcpsimmons/xprune
# for Postgres storage:
pip install "xprune[postgres] @ git+https://github.com/jcpsimmons/xprune"
```

Or clone the repo and run `python -m xprune`.

## Use

```sh
# How many posts are older than 90 days, and roughly what it costs
xprune count --older-than 90

# Archive them (no deletes) and look at what you have
xprune run --older-than 90
xprune status

# Delete one post first, to check your token and permissions
xprune delete --limit 1 --yes

# Delete everything that is archived
xprune delete --yes

# Or do both in one step, which is what you schedule
xprune run --older-than 90 --yes
```

`run` without `--yes` is a dry run: it archives and stops. Each run starts from the newest post already in the archive, so a missed day costs nothing extra; the next run picks up everything since. Use `--since 2024-01-01T00:00:00Z` to set the window yourself.

Storage defaults to `./xprune.db`. Point it elsewhere with `--db` or `XPRUNE_DB`:

```sh
xprune --db ~/x-archive.db run --yes
xprune --db "postgresql://user:pass@host/db" run --yes
```

`--raw-dir DIR` also saves every API page as JSON, if you want the untouched responses.

## Schedule it

Once a day is plenty. Cron example (runs at 10:00):

```cron
0 10 * * * . $HOME/.xprune.env && /usr/local/bin/xprune --db $HOME/x-archive.db run --older-than 90 --yes >> $HOME/xprune.log 2>&1
```

A lock file (`~/.xprune.lock`, or `XPRUNE_LOCK`) keeps two runs from deleting at the same time. The machine has to be awake at that time; cron does not catch up on missed runs, but the next run will.

## The archive

One table, `xprune_posts`:

| Column | What it holds |
|---|---|
| `post_id`, `author_id`, `created_at` | identity and date |
| `post_type` | `original`, `reply`, `quote`, or `repost` |
| `text` | full text, using the long-post text when there is one |
| `referenced_post_id`, `referenced_posts` | the post it replied to, quoted, or reposted, and that post's full object when X returned it |
| `impression_count` and other metrics | snapshot at archive time |
| `media` | media metadata and URLs (files are not downloaded) |
| `payload` | the raw X API post object |
| `delete_status`, `delete_error`, `deleted_at` | `pending`, `deleted`, `not_found`, or `failed` |

Rows that are deleted or failed are never overwritten by a later archive run. `xprune delete --retry-failed --yes` puts failed rows back in the queue.

Each database write opens its own connection. Delete runs sleep for up to 15 minutes at a time, and hosted Postgres (Neon and friends) closes idle connections in that window. Holding one connection open crashed my first run.

## Limits worth knowing

- **Deletes are permanent on X.** The archive is your only copy. Back it up.
- **Media files are not saved,** only their URLs, and those URLs can stop working after the post is gone.
- **Referenced posts can be missing.** If the post you replied to or quoted was already deleted or is private, the archive keeps its ID but not its text.
- **`count` can run high.** X's counts endpoint still counts posts deleted recently, so it may report more than `run` finds.
- **DMs, likes, and bookmarks are not touched.**
- **Pay-per-use accounts are capped at 3 million post reads a month.**
- **Rate limits are per app and user.** Creating extra apps to get around them breaks X's developer terms.

## Development

```sh
python -m unittest discover -s tests
```

MIT licensed.
