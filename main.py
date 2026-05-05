#!/usr/bin/env python3

import time
import random
import json
import os
import re
from typing import List, Dict

from requests import RequestException
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from ytmusicapi import YTMusic
from tqdm import tqdm


def load_env_file(path: str = ".env"):
    if not os.path.exists(path):
        return

    with open(path, "r") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)


load_env_file()

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

YT_HEADERS_FILE = "browser.json"
CACHE_FILE = "cache.json"

DELAY_MIN = 1.5
DELAY_MAX = 4.0
MAX_RETRIES = 10
REQUEST_TIMEOUT_SECONDS = 20
CHUNK_SIZE = 25
RETRY_CHUNK_SIZE = 10
MAX_ADD_PASSES = 3
MISSING_IDS_FILE = "missing_video_ids.json"


def create_spotify_client():
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        raise RuntimeError(
            "Missing Spotify credentials. Set SPOTIFY_CLIENT_ID and "
            "SPOTIFY_CLIENT_SECRET in a .env file or environment variables."
        )
    auth = SpotifyClientCredentials(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET
    )
    return spotipy.Spotify(auth_manager=auth)


def create_ytmusic_client():
    yt = YTMusic(YT_HEADERS_FILE)

    original_request = yt._session.request

    def request_with_timeout(method, url, **kwargs):
        kwargs.setdefault("timeout", REQUEST_TIMEOUT_SECONDS)
        return original_request(method, url, **kwargs)

    yt._session.request = request_with_timeout
    return yt


def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def unique_in_order(items):
    seen = set()
    unique_items = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique_items.append(item)
    return unique_items


def normalize_playlist_input(value: str) -> str:
    value = value.strip()
    match = re.search(r"(https?://open\.spotify\.com/playlist/[^\s]+)", value)
    if match:
        return match.group(1)
    return value


def get_spotify_playlist_metadata(sp, playlist_url: str) -> Dict[str, str]:
    try:
        playlist = sp.playlist(playlist_url, fields="name")
    except spotipy.exceptions.SpotifyException as e:
        if e.http_status == 404:
            raise RuntimeError(
                "The Spotify playlist is private or inaccessible. "
                "Please make sure the playlist is public and that your Spotify credentials have access to it."
            ) from e
        if e.http_status == 400:
            raise RuntimeError(
                "Invalid Spotify playlist URL. "
                "Please paste a URL like https://open.spotify.com/playlist/<id>."
            ) from e
        raise

    return {
        "name": playlist.get("name") or "Imported Playlist",
    }


def get_all_tracks(sp, playlist_url: str) -> List[Dict]:
    try:
        results = sp.playlist_items(playlist_url)
        tracks = results['items']

        while results['next']:
            results = sp.next(results)
            tracks.extend(results['items'])
    except spotipy.exceptions.SpotifyException as e:
        if e.http_status == 404:
            raise RuntimeError(
                "The Spotify playlist is private or inaccessible. "
                "Please make sure the playlist is public and that your Spotify credentials have access to it."
            ) from e
        if e.http_status == 400:
            raise RuntimeError(
                "Invalid Spotify playlist URL. "
                "Please paste a URL like https://open.spotify.com/playlist/<id>."
            ) from e
        raise

    cleaned = []
    for item in tracks:
        track = item.get('track')
        if not track:
            continue

        name = track['name']
        artists = ', '.join([a['name'] for a in track['artists']])

        cleaned.append({
            'query': f"{artists} - {name}",
        })

    seen = set()
    unique = []
    for t in cleaned:
        if t['query'] not in seen:
            seen.add(t['query'])
            unique.append(t)

    return unique


def retry_search(yt, query):
    for attempt in range(MAX_RETRIES):
        try:
            results = yt.search(query, filter="songs")

            if results:
                return results[0]['videoId']

        except (json.JSONDecodeError, RequestException):
            wait = (2 ** attempt) + random.uniform(0, 2)
            time.sleep(wait)

        except Exception:
            time.sleep(2)

    return None


def search_all_tracks(yt, tracks):
    cache = load_cache()
    video_ids = []

    progress = tqdm(total=len(tracks), ncols=100, dynamic_ncols=True)

    for track in tracks:
        query = track['query']

        progress.set_description(f"{query[:50]:<50}")

        if query in cache:
            vid = cache[query]
        else:
            vid = retry_search(yt, query)
            cache[query] = vid
            save_cache(cache)

        if vid:
            video_ids.append(vid)

        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
        progress.update(1)

    progress.close()
    return video_ids


def retry_yt_action(action, name):
    for attempt in range(MAX_RETRIES):
        try:
            return action()
        except (json.JSONDecodeError, RequestException) as e:
            wait = (2 ** attempt) + random.uniform(0, 2)
            if attempt < MAX_RETRIES - 1:
                print(f"\n{name} failed (network error). Attempt {attempt + 1}/{MAX_RETRIES}. Waiting {wait:.1f}s...")
            time.sleep(wait)
        except Exception as e:
            wait = (2 ** attempt)
            if attempt < MAX_RETRIES - 1:
                print(f"\n{name} error: {str(e)[:100]}. Attempt {attempt + 1}/{MAX_RETRIES}. Waiting {wait}s...")
            time.sleep(wait)

    raise RuntimeError(f"{name} failed after {MAX_RETRIES} attempts")


def add_video_ids_in_chunks(yt, playlist_id, video_ids, label, chunk_size):
    added_count = 0
    failed_chunks = []

    for i in tqdm(range(0, len(video_ids), chunk_size), desc=label, ncols=100, dynamic_ncols=True):
        chunk = video_ids[i:i + chunk_size]
        chunk_num = (i // chunk_size) + 1

        try:
            retry_yt_action(
                lambda chunk=chunk: yt.add_playlist_items(playlist_id, chunk),
                f"Add items (chunk {chunk_num})"
            )
            added_count += len(chunk)
        except Exception as e:
            failed_chunks.append((chunk_num, len(chunk), str(e)))
            print(f"\nChunk {chunk_num} ({len(chunk)} tracks) failed: {str(e)[:60]}...")

        time.sleep(5)

    return added_count, failed_chunks


def get_playlist_video_ids(yt, playlist_id):
    playlist = retry_yt_action(
        lambda: yt.get_playlist(playlist_id, limit=None),
        "Get playlist"
    )
    tracks = playlist.get("tracks", [])
    return [track.get("videoId") for track in tracks if track.get("videoId")]


def create_playlist(yt, title, video_ids):
    print("Creating playlist...")
    description = "Migrated from Spotify by: https://github.com/KaykCaputo/spotify-to-ytmusic-playlist-sync"

    playlist_id = retry_yt_action(
        lambda: yt.create_playlist(title, description),
        "Create playlist"
    )

    if not playlist_id:
        raise RuntimeError("Playlist creation failed")

    print(f"Playlist ID: {playlist_id}")

    desired_video_ids = unique_in_order(video_ids)
    if len(desired_video_ids) != len(video_ids):
        print(f"Deduped video IDs: {len(video_ids)} -> {len(desired_video_ids)}")

    added_count, failed_chunks = add_video_ids_in_chunks(
        yt,
        playlist_id,
        desired_video_ids,
        "Adding",
        CHUNK_SIZE
    )

    failed_count = sum(count for _, count, _ in failed_chunks)
    print(f"\nResult: {added_count} added | {failed_count} failed")
    
    if failed_chunks:
        print("\nFailure details:")
        for chunk_num, count, error in failed_chunks:
            print(f"  - Chunk {chunk_num}: {count} tracks - {error[:80]}")

    existing_ids = get_playlist_video_ids(yt, playlist_id)
    missing_ids = [vid for vid in desired_video_ids if vid not in existing_ids]
    print(f"Playlist now has {len(existing_ids)} tracks. Missing: {len(missing_ids)}")

    for attempt in range(MAX_ADD_PASSES):
        if not missing_ids:
            break

        print(f"Retrying missing items (pass {attempt + 1}/{MAX_ADD_PASSES})...")
        add_video_ids_in_chunks(
            yt,
            playlist_id,
            missing_ids,
            f"Retry {attempt + 1}",
            RETRY_CHUNK_SIZE
        )

        existing_ids = get_playlist_video_ids(yt, playlist_id)
        missing_ids = [vid for vid in desired_video_ids if vid not in existing_ids]
        print(f"After retry {attempt + 1}: Missing {len(missing_ids)}")

    if missing_ids:
        with open(MISSING_IDS_FILE, "w") as f:
            json.dump(missing_ids, f, indent=2)
        print(f"Missing IDs saved to {MISSING_IDS_FILE}")

    return playlist_id


def main():
    playlist_url = normalize_playlist_input(input("Spotify playlist URL: "))

    sp = create_spotify_client()
    yt = create_ytmusic_client()

    print("Fetching Spotify tracks...")
    try:
        metadata = get_spotify_playlist_metadata(sp, playlist_url)
        tracks = get_all_tracks(sp, playlist_url)
    except RuntimeError as e:
        print(f"Error: {e}")
        return
    print(f"Total unique tracks: {len(tracks)}")

    print("Searching on YouTube Music...")
    video_ids = search_all_tracks(yt, tracks)

    print(f"Found: {len(video_ids)} / {len(tracks)}")

    playlist_name = metadata.get("name") or "Imported Playlist"
    playlist_id = create_playlist(yt, playlist_name, video_ids)

    print(f"Done. Playlist: {playlist_id}")


if __name__ == "__main__":
    main()
