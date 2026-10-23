import os

from tools.news.fetch_tweets import DEFAULT_API_BASE, fetch_thread, status_parts
from tools.news.generate_description import assemble_tweet_source

from .errors import PipelineError


def fetch_source(url: str) -> dict:
    _, expected_id = status_parts(url)
    try:
        tweet = fetch_thread(
            url, api_base=os.getenv("FXTWITTER_API_BASE", DEFAULT_API_BASE), timeout=30
        )
        source = assemble_tweet_source(tweet)
    except Exception as exc:
        raise PipelineError(
            "source_fetch", "Could not retrieve the X post and thread."
        ) from exc
    if (
        str(tweet.get("id", "")) != expected_id
        or not str(tweet.get("text", "")).strip()
        or not source.strip()
    ):
        raise PipelineError("source_invalid", "Source ID or source text was invalid.")
    return {"source_url": url, "source_text": source}
