# Voice-channel probe — artefacts

Does personal data survive the trip from speech to transcript, and does the redactor still catch it? The method and
the numbers are in the "Voice channel" section of the main [README](../README.md). Everything here is synthetic: the
turns come from the synthetic customer-service corpus of the PETs Proving Ground, the voices are ElevenLabs
text-to-speech (`eleven_multilingual_v2`), the transcripts come from ElevenLabs Scribe (`scribe_v1`, folder `scribe/`)
and from faster-whisper large-v3 (top level).

| file | what |
|---|---|
| `sample.csv` | the 31 customer turns: turn_id, language, text, gold identifiers (JSON), labels |
| `mcp_batches.json` | what was sent to text-to-speech: 4 single turns and 5 grouped recordings (turns separated by 1.5 s breaks) |
| `audio/T*.mp3` | one clip per turn: the 4 singles as generated, the other 27 cut out of the grouped recordings at the chosen boundaries |
| `audio/B01.mp3` … `B05.mp3` | the grouped recordings as generated (B01 English, B02–B04 Finnish, B05 Swedish) |
| `transcripts.json` | turn → its faster-whisper large-v3 transcript (the turn's segment of the recording; boundaries chosen by similarity to the original turns) |
| `recording_transcripts.json` | turn → transcript of the whole recording the turn was spoken in (used for the "whole recording redacted in one piece" column) |
| `stt_meta.json` | recogniser, model, device, seconds, how each recording was split |
| `results.csv` | per turn: original, transcript, adapted transcript (`voice.asr_adapt`), redacted transcript, gold identifiers, values that came back out of speech-to-text, left after redaction (adapter), left without the adapter, left when the whole recording is redacted in one piece |
| `scores.json` | totals and per-label counts (values, survived_stt, leaked_after_redaction, leaked_raw_redaction, leaked_whole_recording, text_channel_leaked) |
| `scribe/` | the same five files for ElevenLabs Scribe (`tools/scribe_stt.py`; `stt_meta.json` also has the detected language and its probability per recording) |

Scoring is per turn, like the text channel: each turn's segment is redacted on its own and each gold value is looked for
in the redacted segment after normalisation (digit words, compound numerals, spoken dates → digits; spelling separators
and spaces removed). Re-score without any API key: `python run.py voice --offline` (it copies this folder to
`outputs/voice/` when that folder has no transcripts yet); for Scribe copy `scribe/*` plus `sample.csv` to
`outputs/voice_scribe/` and run `python run.py voice --offline --subdir voice_scribe`. Re-transcribe the audio:
`python tools/whisper_stt.py --model large-v3` (faster-whisper, torch, ffmpeg) or `python tools/scribe_stt.py`
(`ELEVENLABS_API_KEY` in `.env`). Run the whole thing against the ElevenLabs API from scratch: `python run.py voice`.
