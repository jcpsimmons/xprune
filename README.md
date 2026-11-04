# xprune

Delete your X posts older than N days through the official X API. One command, no dependencies, safe to run from cron.

I wrote this to wipe everything older than 90 days off my own account. It chewed through about 4,700 posts in a day without a single failure, and now it runs once a day while I do something better with my life. Your timeline becomes a rolling window instead of a permanent record.

No browser automation, no scraping, no third-party service holding your tokens. It talks to X's API with your own keys, from your own machine, and it is under 400 lines of standard-library Python you can read in one sitting.

## What it costs

X's API is pay-per-use, so deleting your past costs real money. Prices from the [X pricing page](https://docs.x.com/x-api/getting-started/pricing), checked October 2026:

| Step | Endpoint | Price |
|---|---|---|
| Find your old posts | `GET /2/tweets/search/all` | $0.005 per post |
| Delete a post | `DELETE /2/tweets/:id` | about $0.010 per request |
| Undo a repost | `DELETE /2/users/:id/retweets/:id` | about $0.010 per request |
| Count | `GET /2/tweets/counts/all` | $0.010 per request (about 30 days per request) |

Call it $15 per 1,000 old posts. xprune asks only for the fields it needs to delete, so X bills you for the posts and nothing else. Run `xprune count` first and you get an estimate before you spend a cent. The developer console shows what you were actually charged.

## How long it takes

X allows 50 deletes per 15 minutes on each delete endpoint. Posts and reposts go through different endpoints with separate limits, so xprune runs both at once. Expect about 200 deletes an hour, plus however many of your posts are reposts. When it hits a rate limit it sleeps until the window resets and keeps going. Leave it alone.

## Setup

1. Create an app in the [X developer console](https://console.x.com) and buy some credits.
2. Under **User authentication settings**, set **App permissions** to **Read and write**.
3. On **Keys and tokens**, generate the API key and secret, the bearer token, and the access token and secret. If you change permissions later, **regenerate the access token**. A token made before the change keeps its old read-only access, and X will answer every delete with 403. xprune checks for this and refuses to start a real run (before it spends anything on searching) if the token can't write.
4. Export the five values:

```sh
export X_CONSUMER_KEY=...
export X_CONSUMER_SECRET=...
export X_ACCESS_TOKEN=...
export X_ACCESS_TOKEN_SECRET=...
export X_BEARER_TOKEN=...
```

## Install

Python 3.10 or newer. Standard library only, nothing else to install.

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

`delete` without `--yes` is a dry run. It lists what it found and stops. The dry run still pays for reading your posts, because finding them is the billed part.

Do the `--limit 1` run first. It searches from the oldest end of your history and stops as soon as it has enough posts, so the test costs pennies instead of the price of reading your whole account.

Every run searches your history up to the cutoff, and deleted posts don't come back, so a run that dies halfway just picks up where it left off next time. Use `--since 2024-01-01T00:00:00Z` to narrow the window.

## Schedule it

Once a day is plenty. Cron example (runs at 10:00):

```cron
0 10 * * * . $HOME/.xprune.env && /usr/local/bin/xprune delete --older-than 90 --yes >> $HOME/xprune.log 2>&1
```

A lock file (`~/.xprune.lock`, or `XPRUNE_LOCK`) keeps two runs from deleting at the same time. The machine has to be awake when cron fires. Cron does not catch up on missed runs, but the next run sweeps up anything it missed.

## Read this before you run it

- **Deletes are permanent.** xprune keeps no copy. If you want one, [download your X archive](https://x.com/settings/download_your_data) first. Seriously, do that first.
- **`count` can run high.** X's counts endpoint still counts posts deleted recently, so it may report more than `delete` finds.
- **Search coverage is X's.** xprune deletes what full-archive search returns for `from:you`. If X doesn't index a post there, xprune can't find it.
- **DMs, likes, and bookmarks are not touched.**
- **Pay-per-use accounts are capped at 3 million post reads a month.**
- **Rate limits are per app and user.** Spinning up extra apps to get around them breaks X's developer terms. Be patient instead.

## Development

```sh
python -m unittest discover -s tests
```

MIT licensed.
