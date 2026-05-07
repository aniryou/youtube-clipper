# youtube-clipper

## Run with Docker

The Docker image bundles Python, `yt-dlp`, `youtube-transcript-api`, and a
libass-enabled ffmpeg, so you don't need any of these installed on the host.

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
