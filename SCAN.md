# Verdict

## Performance

Two optimisations keep scans fast even across large lossless libraries:

**Single ffmpeg call per file.** The three measurements (overall RMS, HF low
band, HF high band) are combined into one `filter_complex` graph with
`asplit=3`.  The file is decoded once instead of three times — roughly 2–3×
faster per file than sequential calls.

**Thread pool.** File processing runs on `os.cpu_count()` workers by default
(GIL released during subprocess calls).  Override with `-j N` / `--jobs N`.

## Methodology

See `src/music/verdict.py` for the full methodology docstring and tunable
thresholds (`BRICKWALL_DROP_DB`, `LOW_ENERGY_DB`, `NO_HF_DB`).

## Potential improvements

These are documented so they don't get lost.  Implementation priority is a
rough ordering by leverage-to-effort ratio.

### 1. Multi-point slope profile

Measure RMS at 4–5 frequency points instead of two (e.g. 15k, 17k, 19k,
20.5k, 21.5k) and flag the steepest adjacent drop.  This catches brickwalls at
any encoder cutoff — including 128 kbps MP3 at 16 kHz, which the current
two-point test correctly flags as "Inconclusive" but doesn't actively identify.

```
True lossless:       -35, -40, -48, -55, -62  (gradual, ~7–8 dB per step)
320 kbps transcode:  -35, -38, -42, -85, -90  (cliff at 19→20.5k = 43 dB)
128 kbps transcode:  -35, -85, -88, -90, -92  (cliff at 15→17k = 50 dB)
```

Just more highpass branches in the `filter_complex` graph — same architecture.

### 2. Peak-hold instead of RMS over time

`astats` gives RMS of the entire 60-second window.  A track with 30 seconds of
sparse intro and 30 seconds of full-bandwidth climax gets its HF signature
diluted by 50%.  Peak-hold — the maximum energy at each band at *any* moment —
is more discriminating.

`astats` outputs `Peak_level` alongside `RMS_level`.  Switching (or taking
both) is a one-line change in the filter chain.

### 3. Spectral flatness in the transition zone

A codec brickwall changes the *character* of the signal above the cutoff.
Genuine ultrasonic content is structured (harmonics, overtones, room
resonance); quantization noise (AAC/Opus noise substitution) is flat and
featureless.

Sampling several narrow bandpass slices in the 20.5–21.5 kHz zone and computing
the variance across them would distinguish musical content from codec noise.

### 4. Noise floor profiling

Different formats leave different noise signatures near Nyquist:
- 24-bit: noise floor around -144 dB (theoretical)
- 16-bit: noise floor around -96 dB (dither)
- Lossy transcode: elevated, modulated — often -60 to -70 dB in the highs

Measuring RMS at Nyquist (22–22.05 kHz for 44.1 kHz material) gives a quick
noise-floor estimate.

### 5. Hi-res authenticity

For sample rates above 48 kHz: is there content above 22.05 kHz at all?  A 96
kHz FLAC with nothing above 22 kHz is an upsample from 44.1/48 kHz material.
A single highpass at 25 kHz + astats answers this trivially.

### 6. User-tunable sensitivity

The thresholds are already named constants.  Exposing them via CLI flags
(`--brickwall-threshold`, `--low-energy-db`) would let power users tune the
detector for their library and risk tolerance.
