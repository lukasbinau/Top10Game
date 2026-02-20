// sounds.js – Synthesized party-game audio for Top10 Game
// Uses Web Audio API only – no external files needed

window.GameAudio = (() => {
  "use strict";

  let ctx = null;
  let masterGain, musicGain, sfxGain;
  let muted = false;
  let musicPlaying = false;
  let schedulerTimer = null;
  let nextBeatTime = 0;
  let currentBeat = 0;

  const BPM = 128;
  const BEAT = 60 / BPM;
  const LOOKAHEAD = 0.12;
  const INTERVAL = 25;

  // ─── AudioContext (lazy, needs user gesture) ───

  function ensure() {
    if (!ctx) {
      ctx = new (window.AudioContext || window.webkitAudioContext)();

      masterGain = ctx.createGain();
      masterGain.gain.value = muted ? 0 : 1;
      masterGain.connect(ctx.destination);

      musicGain = ctx.createGain();
      musicGain.gain.value = 0.18;
      musicGain.connect(masterGain);

      sfxGain = ctx.createGain();
      sfxGain.gain.value = 0.5;
      sfxGain.connect(masterGain);
    }
    if (ctx.state === "suspended") ctx.resume();
    return ctx;
  }

  // ─── Synthesis primitives ───

  function tone(freq, type, dur, vol, t, dest) {
    const c = ensure();
    const o = c.createOscillator();
    const g = c.createGain();
    o.type = type;
    o.frequency.setValueAtTime(freq, t);
    g.gain.setValueAtTime(vol, t);
    g.gain.exponentialRampToValueAtTime(0.001, t + dur);
    o.connect(g);
    g.connect(dest || sfxGain);
    o.start(t);
    o.stop(t + dur + 0.02);
  }

  function chordTone(freqs, type, dur, vol, t, dest) {
    const v = vol / Math.sqrt(freqs.length);
    freqs.forEach(f => tone(f, type, dur, v, t, dest));
  }

  function noiseBurst(dur, vol, t, hpFreq, dest) {
    const c = ensure();
    const len = Math.max(Math.floor(c.sampleRate * dur), 2);
    const buf = c.createBuffer(1, len, c.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
    const src = c.createBufferSource();
    src.buffer = buf;
    const g = c.createGain();
    g.gain.setValueAtTime(vol, t);
    g.gain.exponentialRampToValueAtTime(0.001, t + dur);
    if (hpFreq) {
      const f = c.createBiquadFilter();
      f.type = "highpass";
      f.frequency.value = hpFreq;
      src.connect(f);
      f.connect(g);
    } else {
      src.connect(g);
    }
    g.connect(dest || sfxGain);
    src.start(t);
    src.stop(t + dur + 0.02);
  }

  // ─── Music primitives ───

  function musicKick(t) {
    const c = ensure();
    const o = c.createOscillator();
    const g = c.createGain();
    o.type = "sine";
    o.frequency.setValueAtTime(150, t);
    o.frequency.exponentialRampToValueAtTime(35, t + 0.12);
    g.gain.setValueAtTime(0.7, t);
    g.gain.exponentialRampToValueAtTime(0.001, t + 0.2);
    o.connect(g);
    g.connect(musicGain);
    o.start(t);
    o.stop(t + 0.25);
  }

  function musicHihat(t, vol) {
    noiseBurst(0.04, vol || 0.1, t, 8000, musicGain);
  }

  // ─── Music pattern data ───
  // I – vi – IV – V in C  (upbeat pop progression)

  const CHORDS = [
    [262, 330, 392],  // C  (C4 E4 G4)
    [220, 262, 330],  // Am (A3 C4 E4)
    [175, 220, 262],  // F  (F3 A3 C4)
    [196, 247, 294],  // G  (G3 B3 D4)
  ];
  const BASS = [131, 110, 87, 98]; // C3 A2 F2 G2

  // 8-beat melody (0 = rest), repeats every 8 beats
  const MELODY = [659, 0, 784, 659, 523, 0, 587, 0];

  function scheduleBeat(beatNum, t) {
    const barBeat = beatNum % 16;
    const bar = Math.floor(barBeat / 4);
    const beat = barBeat % 4;
    const ch = CHORDS[bar];
    const bass = BASS[bar];

    // Kick on 0 & 2
    if (beat === 0 || beat === 2) musicKick(t);

    // Hi-hat on every beat + offbeat
    musicHihat(t, 0.1);
    musicHihat(t + BEAT * 0.5, 0.055);

    // Open hi-hat variation on beat 3 offbeat
    if (beat === 3) noiseBurst(0.07, 0.07, t + BEAT * 0.5, 6000, musicGain);

    // Chord stabs
    if (beat === 0) chordTone(ch, "triangle", BEAT * 0.35, 0.2, t, musicGain);
    if (beat === 1) chordTone(ch, "triangle", BEAT * 0.25, 0.12, t + BEAT * 0.5, musicGain);
    if (beat === 2) chordTone(ch, "triangle", BEAT * 0.35, 0.16, t, musicGain);

    // Bass (syncopated)
    if (beat === 0) tone(bass, "sine", BEAT * 0.6, 0.35, t, musicGain);
    if (beat === 2) tone(bass, "sine", BEAT * 0.4, 0.25, t + BEAT * 0.5, musicGain);
    if (beat === 3) tone(bass * 1.5, "sine", BEAT * 0.3, 0.18, t, musicGain);

    // Melody
    const mel = MELODY[barBeat % 8];
    if (mel) tone(mel, "sine", BEAT * 0.28, 0.09, t, musicGain);
  }

  function scheduler() {
    if (!musicPlaying) return;
    const c = ensure();
    while (nextBeatTime < c.currentTime + LOOKAHEAD) {
      scheduleBeat(currentBeat, nextBeatTime);
      nextBeatTime += BEAT;
      currentBeat++;
    }
  }

  // ─── Sound effects ───

  const sfx = {

    teamAdd() {
      const c = ensure(), t = c.currentTime;
      tone(660, "sine", 0.08, 0.22, t);
      tone(880, "triangle", 0.14, 0.28, t + 0.06);
    },

    teamRemove() {
      const c = ensure(), t = c.currentTime;
      tone(600, "sine", 0.08, 0.18, t);
      tone(400, "triangle", 0.14, 0.15, t + 0.06);
    },

    gameStart() {
      const c = ensure(), t = c.currentTime;
      tone(523, "square", 0.1, 0.12, t);       // C5
      tone(659, "square", 0.1, 0.12, t + 0.1); // E5
      tone(784, "square", 0.1, 0.12, t + 0.2); // G5
      tone(1047, "square", 0.3, 0.18, t + 0.3); // C6
      tone(1568, "sine", 0.35, 0.06, t + 0.3);  // sparkle
      noiseBurst(0.15, 0.06, t + 0.3, 4000);
    },

    nextRound() {
      const c = ensure(), t = c.currentTime;
      tone(440, "triangle", 0.09, 0.16, t);
      tone(554, "triangle", 0.09, 0.16, t + 0.07);
      tone(659, "triangle", 0.14, 0.2, t + 0.14);
    },

    submitGuess() {
      const c = ensure(), t = c.currentTime;
      tone(800, "sine", 0.05, 0.14, t);
      tone(1000, "sine", 0.09, 0.12, t + 0.04);
    },

    reveal() {
      const c = ensure(), t = c.currentTime;
      // Quick drum-roll build-up
      for (let i = 0; i < 10; i++) {
        noiseBurst(0.055, 0.04 + i * 0.012, t + i * 0.055, 3000);
      }
      tone(220, "sawtooth", 0.55, 0.04, t);
    },

    hitMiss() {
      const c = ensure(), t = c.currentTime;
      tone(250, "sawtooth", 0.15, 0.1, t);
      tone(200, "sawtooth", 0.22, 0.08, t + 0.1);
    },

    hitGood() {
      const c = ensure(), t = c.currentTime;
      tone(523, "sine", 0.1, 0.2, t);
      tone(659, "sine", 0.18, 0.22, t + 0.07);
    },

    hitGreat() {
      const c = ensure(), t = c.currentTime;
      tone(523, "sine", 0.09, 0.18, t);
      tone(659, "sine", 0.09, 0.18, t + 0.07);
      tone(784, "sine", 0.22, 0.22, t + 0.14);
    },

    hitPerfect() {
      const c = ensure(), t = c.currentTime;
      // Big celebration
      tone(523, "square", 0.09, 0.12, t);
      tone(659, "square", 0.09, 0.12, t + 0.07);
      tone(784, "square", 0.09, 0.12, t + 0.14);
      tone(1047, "square", 0.3, 0.16, t + 0.21);
      tone(1319, "sine", 0.28, 0.08, t + 0.21);
      tone(1568, "sine", 0.22, 0.06, t + 0.28);
      noiseBurst(0.25, 0.1, t + 0.21, 3000);  // crash
    },

    skip() {
      const c = ensure(), t = c.currentTime;
      tone(500, "sine", 0.06, 0.1, t);
      tone(350, "sine", 0.1, 0.08, t + 0.05);
    },

    click() {
      const c = ensure(), t = c.currentTime;
      noiseBurst(0.025, 0.08, t, 3000);
    }
  };

  // ─── Reveal with delayed hit sound ───

  function playRevealResult(bestPoints) {
    if (muted) return;
    try { sfx.reveal(); } catch (e) { /* ignore */ }
    setTimeout(() => {
      try {
        if (bestPoints >= 10) sfx.hitPerfect();
        else if (bestPoints >= 7) sfx.hitGreat();
        else if (bestPoints >= 1) sfx.hitGood();
        else sfx.hitMiss();
      } catch (e) { /* ignore */ }
    }, 620);
  }

  // ─── Music controls ───

  function startMusic() {
    if (musicPlaying) return;
    ensure();
    musicPlaying = true;
    currentBeat = 0;
    nextBeatTime = ctx.currentTime + 0.05;
    musicGain.gain.setValueAtTime(0, ctx.currentTime);
    musicGain.gain.linearRampToValueAtTime(0.18, ctx.currentTime + 1.5);
    schedulerTimer = setInterval(scheduler, INTERVAL);
  }

  function stopMusic() {
    musicPlaying = false;
    if (schedulerTimer) { clearInterval(schedulerTimer); schedulerTimer = null; }
    if (musicGain && ctx) {
      musicGain.gain.cancelScheduledValues(ctx.currentTime);
      musicGain.gain.setValueAtTime(musicGain.gain.value, ctx.currentTime);
      musicGain.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.6);
    }
  }

  function toggleMute() {
    muted = !muted;
    if (masterGain && ctx) {
      masterGain.gain.setValueAtTime(muted ? 0 : 1, ctx.currentTime);
    }
    return muted;
  }

  // ─── Public API ───

  function play(name) {
    if (muted) return;
    if (sfx[name]) {
      try { sfx[name](); } catch (e) { console.warn("SFX error:", e); }
    }
  }

  return {
    play,
    playRevealResult,
    startMusic,
    stopMusic,
    toggleMute,
    isMuted: () => muted,
    isMusicPlaying: () => musicPlaying,
    ensure,
  };
})();
