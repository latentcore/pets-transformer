# Voice-channel probe — artefacts

Does personal data survive the trip from speech to transcript, and does the redactor still catch it? The method and
the numbers are in the "Voice channel" section of the main [README](../README.md). Everything here is synthetic: the
turns come from the synthetic customer-service corpus of the PETs Proving Ground, the voices are ElevenLabs
text-to-speech (`eleven_multilingual_v2`), the transcripts are faster-whisper large-v3.

| file | what |
|---|---|
| `sample.csv` | the 31 customer turns: turn_id, language, text, gold identifiers (JSON), labels |
| `mcp_batches.json` | what was sent to text-to-speech: 4 single turns and 5 grouped recordings (turns separated by 1.5 s breaks) |
| `audio/T*.mp3` | one clip per turn: the 4 singles as generated, the other 27 cut out of the grouped recordings at the chosen boundaries |
| `audio/B01.mp3` … `B05.mp3` | the grouped recordings as generated (B01 English, B02–B04 Finnish, B05 Swedish) |
| `recording_transcripts.json` | turn → transcript of the whole recording the turn was spoken in — **this is what the scores use** |
| `transcripts.json` | turn → its segment of that transcript (display and clips only; boundaries chosen by similarity to the original turns) |
| `stt_meta.json` | recogniser, model, device, seconds, how each recording was split |
| `results.csv` | per turn: original, transcript, adapted transcript (`voice.asr_adapt`), redacted transcript, gold identifiers, values that came back out of speech-to-text, left after redaction, left without the adapter |
| `scores.json` | totals and per-label counts (values, survived_stt, leaked_after_redaction, leaked_raw_redaction, text_channel_leaked) |

Re-score without any API key: `python run.py voice --offline` (it copies this folder to `outputs/voice/` when that
folder has no transcripts yet). Re-transcribe the audio: `python tools/whisper_stt.py --model large-v3`
(faster-whisper, torch, ffmpeg). Run the whole thing against the ElevenLabs API, Scribe included:
`python run.py voice` with `ELEVENLABS_API_KEY` in `.env`.
