/**
 * AudioWorklet processor – runs in the dedicated audio rendering thread,
 * never on the main JS thread.
 *
 * Accumulates raw Float32 samples and transfers them to the main thread as
 * transferable ArrayBuffers (zero-copy).  The main thread is responsible for
 * downsampling, PCM-16 conversion, and base64 encoding.
 *
 * CHUNK_SIZE is tuned so that one chunk ≈ 85 ms at 48 kHz (typical system
 * sample rate), which is a good trade-off between latency and overhead.
 */

const CHUNK_SIZE = 4096;   // samples per message

class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buf = new Float32Array(CHUNK_SIZE);
    this._pos = 0;
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;

    for (let i = 0; i < channel.length; i++) {
      this._buf[this._pos++] = channel[i];

      if (this._pos === CHUNK_SIZE) {
        // Transfer ownership – avoids copying the buffer
        this.port.postMessage(this._buf.buffer, [this._buf.buffer]);
        this._buf = new Float32Array(CHUNK_SIZE);
        this._pos = 0;
      }
    }

    return true;  // keep processor alive
  }
}

registerProcessor('mic-processor', MicProcessor);
