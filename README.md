# youtube-clipper

[![CI](https://github.com/aniryou/youtube-clipper/actions/workflows/ci.yml/badge.svg)](https://github.com/aniryou/youtube-clipper/actions/workflows/ci.yml)

Cut clips from a YouTube video, optionally join several with a crossfade,
and burn the transcript in as captions.

## Usage

All examples below use the locally installed script (`python yt_clipper.py …`).
For Docker, prefix with `docker run --rm -v "$PWD":/work ghcr.io/aniryou/youtube-clipper:latest`
and drop the `python yt_clipper.py` (the image's entrypoint is the script itself).

### Single clip

```sh
python yt_clipper.py "https://youtu.be/abc" --start 1:30 --end 2:45
```

`--start` / `--end` accept `mm:ss` or `hh:mm:ss`.

### Multiple clips (joined with a crossfade)

Pass several values to `--start` and `--end` — they are paired positionally,
so the *N*th `--start` matches the *N*th `--end`:

```sh
python yt_clipper.py "https://youtu.be/abc" \
  --start 1:00 3:30 8:10 \
  --end   1:45 4:15 9:00
```

This produces three clips (`1:00–1:45`, `3:30–4:15`, `8:10–9:00`) joined in
order with a crossfade between each. Tune the crossfade duration with
`--fade` (default: `0.5` seconds; use `--fade 0` to hard-cut).

### Reordering clips

**Clips are emitted in the exact order you list them on the command line.**
No sorting is applied, so to play a later moment first, just list it first:

```sh
# Plays 8:10–9:00 first, then 1:00–1:45, then 3:30–4:15
python yt_clipper.py "https://youtu.be/abc" \
  --start 8:10 1:00 3:30 \
  --end   9:00 1:45 4:15
```

The same goes for repeating a clip — list its timestamps twice and it will
appear twice in the output.

### Keyword-based clipping

Auto-clip around the first transcript hit for a phrase:

```sh
python yt_clipper.py "https://youtu.be/abc" \
  --keyword "vibe coding" \
  --padding 15 \
  --subtitle-style tarantino
```

`--padding` adds seconds of context on either side of the matched line.

### Other useful flags

- `--no-captions` — skip burning the transcript into the video.
- `--list-transcript` — print the full transcript and exit (no download).
- `--subtitle-style {default,tarantino}` — caption look.
- `--output-dir DIR` / `--output-name NAME` — control where the `.mp4` lands.

Run `python yt_clipper.py --help` for the full reference.

## Run with Docker

The Docker image bundles Python, `yt-dlp`, `youtube-transcript-api`, and a
libass-enabled ffmpeg, so you don't need any of these installed on the host.

### Pull from GHCR

Prebuilt images are published to the GitHub Container Registry on every
`vX.Y.Z` tag. Pull the latest release:

```sh
docker pull ghcr.io/aniryou/youtube-clipper:latest
```

Or pin to a specific version:

```sh
docker pull ghcr.io/aniryou/youtube-clipper:v0.1.0
```

Run it the same way as a locally built image:

```sh
docker run --rm -v "$PWD":/work ghcr.io/aniryou/youtube-clipper:latest "<youtube-url>" --start 1:00 --end 2:00
```

### Build locally

Build the image:

```sh
docker build -t youtube-clipper .
```

Show the CLI help:

```sh
docker run --rm youtube-clipper --help
```

Cut a clip and write it to the host's current directory:

```sh
docker run --rm -v "$PWD":/work youtube-clipper "<youtube-url>" --start 1:00 --end 2:00
```

The container's working directory is `/work` and the script's `--output-dir`
defaults to `.`, so mounting the host cwd at `/work` causes the resulting
`.mp4` to land alongside your shell prompt.

The image runs as a non-root `clipper` user (UID 1000) so output files
aren't owned by `root`. On Linux hosts where your user's UID is not 1000,
override it so the mounted directory is writable and files land with your
ownership:

```sh
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/work youtube-clipper "<youtube-url>" --start 1:00 --end 2:00
```

## CI

Every pull request and push to `main` runs `ruff` lint, `pytest`, and a
Docker build via [`.github/workflows/ci.yml`](.github/workflows/ci.yml).
Pushing a `vX.Y.Z` tag additionally publishes the image to
`ghcr.io/aniryou/youtube-clipper:vX.Y.Z` and `:latest`.

To run the same checks locally:

```sh
pip install -r requirements-dev.txt
ruff check .
pytest -q
```
