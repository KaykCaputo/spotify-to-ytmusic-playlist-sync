# Spotify to YouTube Music Playlist Script

Simple Python script to copy a playlist from Spotify to YouTube Music.

## Requirements

-   Python 3.10+
-   Spotify account
-   YouTube Music account

## Install

``` 
pip install -r requirements.txt 
```

## Setup

### Spotify

Create an app at: https://developer.spotify.com/dashboard

Copy .env.example to .env and fill your credentials:

```
SPOTIFY_CLIENT_ID  
SPOTIFY_CLIENT_SECRET
```

### YouTube Music

Run: 
```
ytmusicapi browser
```

Copy request headers from your browser (Network tab). This will create:
browser.json

## Usage

Run:

``` 
python3 main.py  
```

Paste your Spotify playlist URL.

## Notes

-   Some songs may not be found
-   Script uses delays to avoid blocking
-   Large playlists take time
-   cache.json stores search results to speed up future runs
-   Delete cache.json to force fresh searches
-   Songs are added in chunks of 50 to reduce API failures
-   The new YouTube Music playlist uses the original Spotify playlist name


## Troubleshooting

Error JSONDecodeError: Wait and try again (rate limit)

Error oauth_credentials: Regenerate browser.json
