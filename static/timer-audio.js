'use strict';
// Sounds require an open browser page and an awake computer.
class RiverTimerAudio {
  constructor() {
    this.context = null;
    this.enabled = (window.RiverMemory || localStorage).getItem('river-timer-sound') !== 'off';
    try { this.heard = new Set(JSON.parse((window.RiverMemory || localStorage).getItem('river-timer-heard') || '[]')); }
    catch { this.heard = new Set(); }
    this.shown = new Set();
  }
  async arm() {
    if (!this.enabled) return false;
    try {
      this.context ||= new (window.AudioContext || window.webkitAudioContext)();
      if (this.context.state === 'suspended') await this.context.resume();
      return this.context.state === 'running';
    } catch { return false; }
  }
  chime() {
    if (!this.enabled || this.context?.state !== 'running') return false;
    for (const [index,frequency] of [660,830,990].entries()) {
      const start = this.context.currentTime + index * 0.28;
      const tone = this.context.createOscillator();
      const volume = this.context.createGain();
      tone.type = 'sine'; tone.frequency.value = frequency;
      volume.gain.setValueAtTime(0, start);
      volume.gain.linearRampToValueAtTime(0.16, start + 0.025);
      volume.gain.exponentialRampToValueAtTime(0.001, start + 0.45);
      tone.connect(volume); volume.connect(this.context.destination);
      tone.start(start); tone.stop(start + 0.5);
      tone.onended = () => { tone.disconnect(); volume.disconnect(); };
    }
    return true;
  }
  alert(key, message, show) {
    if (this.heard.has(key)) return;
    if (!this.shown.has(key)) { show(message); this.shown.add(key); }
    if (!this.enabled || this.chime()) {
      this.heard.add(key);
      this.heard = new Set([...this.heard].slice(-100));
      (window.RiverMemory || localStorage).setItem('river-timer-heard', JSON.stringify([...this.heard]));
    }
  }
  async toggle() {
    this.enabled = !this.enabled;
    (window.RiverMemory || localStorage).setItem('river-timer-sound', this.enabled ? 'on' : 'off');
    if (this.enabled) await this.arm();
  }
}
window.RiverTimerAudio = RiverTimerAudio;
