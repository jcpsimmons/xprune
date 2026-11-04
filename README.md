# xprune

Delete your X posts older than N days through the official X API. One command, no dependencies, safe to run on a schedule.

I built this to clear everything older than 90 days off my account. It went through about 4,700 posts in a day with no failures, and now runs daily.

## What it costs

X's API is pay-per-use. Prices from the [X pricing page](https://docs.x.com/x-api/getting-started/pricing), checked October 2026:

| Step | Endpoint | Price |
|---|---|---|
| Find your old posts | `GET /2/tweets/search/all` | $0.005 per post |
| Delete a post | `DELETE /2/tweets/:id` | about $0.010 per request |
| Undo a repost | `DELETE /2/users/:id/retweets/:id` | about $0.010 per request |
| Count | `GET /2/tweets/counts/all` | $0.010 per request (about 30 days per request) |

So 1,000 old posts cost about $15. xprune asks only for the fields it needs to delete, so X bills the posts themselves and nothing else. The developer console shows what you were actually charged. `xprune count` gives you an estimate before you spend anything.

## How long it takes

X allows 50 deletes per 15 minutes on each delete endpoint. Posts and reposts use different endpoints with separate limits, so xprune runs them in parallel. Expect about 200 deletes an hour, plus whatever share of your posts are reposts. It sleeps through rate-limit windows on its own; leave it running.

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

Python 3.10 or newer, standard library only.

```sh
pip install git+https://github.com/jcpsimmons/xprune
```

Or clone the repo and run `python -m xprune`.

## Use

```sh
# How many posts are older than 90 days, and roughly what deleting them costs
xprune count --older-than 90

# See what would be deleted (dry run)
xprune delete --older-than 90

# Delete the single oldest post, to check your token and permissions
xprune delete --older-than 90 --limit 1 --yes

# Delete everything older than 90 days
xprune delete --older-than 90 --yes

# Only replies and reposts
xprune delete --older-than 30 --types reply,repost --yes
```

`delete` without `--yes` is a dry run: it lists what it found and stops. The dry run still pays for reading your posts. Each run searches your whole history up to the cutoff, and posts that are already gone don't come back, so a run that stops halfway just picks up where it left off next time. Use `--since 2024-01-01T00:00:00Z` to narrow the window.

## Schedule it

Once a day is plenty. Cron example (runs at 10:00):

```cron
0 10 * * * . $HOME/.xprune.env && /usr/local/bin/xprune delete --older-than 90 --yes >> $HOME/xprune.log 2>&1
```

A lock file (`~/.xprune.lock`, or `XPRUNE_LOCK`) keeps two runs from deleting at the same time. The machine has to be awake at that time; cron does not catch up on missed runs, but the next run will.

## Limits worth knowing

- **Deletes are permanent.** xprune keeps no copy. If you want one, [download your X archive](https://x.com/settings/download_your_data) first.
- **`count` can run high.** X's counts endpoint still counts posts deleted recently, so it may report more than `delete` finds.
- **Search coverage is X's.** xprune deletes what full-archive search returns for `from:you`. Posts X doesn't index there won't be found.
- **DMs, likes, and bookmarks are not touched.**
- **Pay-per-use accounts are capped at 3 million post reads a month.**
- **Rate limits are per app and user.** Creating extra apps to get around them breaks X's developer terms.

## Development

```sh
python -m unittest discover -s tests
```

MIT licensed.
