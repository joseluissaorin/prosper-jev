// Captura del micrófono: el AudioContext ya corre a 16 kHz, así que solo hay que pasar a PCM de 16 bits.
class Capture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Int16Array(640); // 40 ms
    this.n = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    let peak = 0;
    for (let i = 0; i < ch.length; i++) {
      const s = Math.max(-1, Math.min(1, ch[i]));
      peak = Math.max(peak, Math.abs(s));
      this.buf[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.n === this.buf.length) {
        this.port.postMessage({ pcm: this.buf.buffer.slice(0), peak });
        this.n = 0;
      }
    }
    return true;
  }
}
registerProcessor("capture", Capture);
