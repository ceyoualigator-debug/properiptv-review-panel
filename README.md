# ProperIpTv — App Review demo panel

An Xtream Codes panel serving [iptv-org](https://iptv-org.github.io/)'s catalogue
of free, publicly available channels.

It exists so App Review has something to sign into. ProperIpTv is a player with
no content of its own, so Apple needs a working account — and handing over a
real subscription is a bad trade: those lines allow one simultaneous connection,
so a reviewer signing in kills the owner's stream and gets logged as "app not
functional", and the credentials sit in App Store Connect in plaintext.

These channels are already public and the credentials protect nothing.

## Run it

    python3 server.py          # http://127.0.0.1:8100

## Deploy it

Anything that runs a container. On Render: New → Web Service → point at this
repo → it reads `render.yaml`. The free plan sleeps when idle and takes ~30s to
wake, which is fine for review.

## Sign-in

| | |
|---|---|
| Server | the deployed URL |
| Username | `demo` |
| Password | `demo` |

Roughly 4,800 live channels across 155 countries, grouped `CC | GENRE` so the
app's country and genre facets have real data to work with. No VOD — the
Movies and Series tabs correctly do not appear.

`/health` returns the channel count and the sign-in details.
