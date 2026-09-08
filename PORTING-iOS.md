# Taking ScoreShift to iOS

The Windows app and a future iPhone app are meant to be two faces of one
engine. This is the map.

## How the code is split

```
scoreshift/
  engine.py          THE ENGINE - clean pages, read notes, transpose, export.
                     No Qt, no Windows, no UI. Everything else is a caller.
  pipeline/
    pagedetect.py    page finding, perspective, deskew, clean-up   (portable)
    score.py         key detection and transposition               (portable)
    export.py        PDF / MIDI / MusicXML                         (portable*)
    omr.py           Audiveris process control                     (server only)
    camera.py        live page tracking + auto-shutter RULES       (portable)
    scanner.py       Windows Image Acquisition                     (Windows only)
  ui/                the Qt desktop app                            (Windows only)
  service/           the JSON API - the same engine over HTTP      (server)
```

\* `export.py` engraves PDFs with MuseScore if present, otherwise Verovio plus
Qt as the page writer. On a server without Qt, install MuseScore or swap in
`verovio` → `cairosvg`.

The rule to keep: **nothing in `engine.py` or `pipeline/` may import from
`ui/`.** As long as that holds, the iOS app is a new caller, not a rewrite.

## What runs where

| Piece | iPhone | Server |
|---|---|---|
| Camera, page outline preview, auto-shutter | ✅ native | |
| Cropping and clean-up | either | ✅ |
| Reading the notes (Audiveris, Java) | ✗ can't | ✅ |
| Transposition (music21) | ✗ | ✅ |
| PDF / MIDI | ✗ | ✅ |
| Viewing and sharing results | ✅ | |

Audiveris is a Java desktop program, so note-reading has to happen off the
phone. That is the whole reason for `scoreshift/service`.

## Running the engine as a service

On the Windows machine, from the Start menu: **ScoreShift API server**, or:

```
ScoreShift.exe --server --host 0.0.0.0 --port 8765
```

Interactive docs appear at `http://<machine>:8765/docs`.

**Before exposing it beyond your own machine**, set a shared secret:

```powershell
setx SCORESHIFT_API_KEY "some-long-random-string"
```

Clients then send `X-API-Key: <that string>`. Without the variable set there is
no authentication at all, which is fine on a laptop and not fine on a network
you don't control.

## The API

| | |
|---|---|
| `GET /v1/health` | is the OMR engine present, which engraver is in use |
| `POST /v1/scores` | multipart `files` (page images, in order) → score summary with `job_id` |
| `GET /v1/scores/{id}` | key, time signature, parts, bars, range |
| `POST /v1/scores/{id}/transpose` | `{target_key}` or `{semitones}` or `{instrument}` |
| `GET /v1/scores/{id}/preview?pages=4` | engraved SVG pages, for showing the result |
| `GET /v1/scores/{id}/download?format=pdf\|midi\|musicxml` | the file |
| `DELETE /v1/scores/{id}` | forget it |

```bash
# The whole round trip
curl -F files=@page1.jpg -F files=@page2.jpg http://host:8765/v1/scores
curl -X POST http://host:8765/v1/scores/$ID/transpose \
     -H 'Content-Type: application/json' -d '{"target_key":"F major"}'
curl -o out.pdf "http://host:8765/v1/scores/$ID/download?format=pdf"
```

Jobs are held in memory and swept after six hours; a restart loses them. If the
iOS app needs history, keep the returned files on the phone, or add a database
behind the same endpoints — the API shape won't have to change.

## The camera on iOS

Don't port the OpenCV tracker. iOS already has the hard part: `Vision`'s
`VNDetectRectanglesRequest` returns page quads per frame, and
`VNDocumentCameraViewController` gives you Apple's whole scanning UI free —
edge detection, perspective correction, multi-page, shutter and all. For a
first version, use `VNDocumentCameraViewController` and upload what it returns.

What *is* worth porting is the judgement in `pipeline/camera.py`, because those
thresholds are what make auto-shutter feel right rather than trigger-happy:

| Rule | Value | Why |
|---|---|---|
| `min_fill` | 0.55 | Fraction of the frame's **width or height** the page spans. Area is the wrong measure — a portrait page in a 16:9 frame tops out near 40% of the area even when perfectly framed. |
| edge margin | ~2 px on the tracking frame | A corner touching the frame edge means the page is cut off: say "move back", never shoot. |
| `min_sharpness` | 22 (variance of Laplacian, inside the page) | Rejects motion blur and a camera still hunting for focus. |
| brightness | 55–248 | Too dark loses staff lines; blown-out white loses them too. |
| `max_motion` | 1.2% of the frame diagonal | Corner movement between frames. |
| `hold_seconds` | 0.7 | Everything must stay good this long before it fires. |
| `cooldown_seconds` | 2.0 | Stops one page being shot five times. |

Order matters as much as the values: report **one** problem, the most useful
one, in this order — cut off → too far → too dark → too bright → blurry → still
moving. A status line that flickers between three complaints is worse than one
that says "move closer".

## If you ever want it fully offline on the phone

That means an on-device OMR model, since Audiveris can't come along. The path
would be: collect scores + ground-truth MusicXML, train or fine-tune an
end-to-end OMR model, convert to Core ML, and keep the server as the fallback
for anything it can't handle. That is a project in its own right — the cloud
path above is what makes the app real first, and it stays useful afterwards as
the accuracy backstop.

## Shared vocabulary

Keep the iOS models named after `engine.py`'s types — `CleanOptions`,
`TransposeRequest`, `ScoreSummary`, `ExportRequest`. When the API grows a field,
both ends change in the same vocabulary, and the Windows app and the phone stay
honest about being one product.
