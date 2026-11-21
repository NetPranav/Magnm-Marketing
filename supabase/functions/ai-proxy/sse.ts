export interface SseUsage {
  promptTokens: number | null;
  completionTokens: number | null;
  textChars: number;
}

export class SseUsageTracker {
  private decoder = new TextDecoder("utf-8");
  private lineBuffer = "";
  private promptTokens: number | null = null;
  private completionTokens: number | null = null;
  private textChars = 0;

  push(chunk: Uint8Array): void {
    this.lineBuffer += this.decoder.decode(chunk, { stream: true });
    this.drainLines(false);
  }

  finish(): SseUsage {
    this.lineBuffer += this.decoder.decode();
    this.drainLines(true);
    return {
      promptTokens: this.promptTokens,
      completionTokens: this.completionTokens,
      textChars: this.textChars,
    };
  }

  private drainLines(flushRemaining: boolean): void {
    let idx = this.lineBuffer.indexOf("\n");
    while (idx !== -1) {
      const line = this.lineBuffer.slice(0, idx);
      this.lineBuffer = this.lineBuffer.slice(idx + 1);
      this.processLine(line);
      idx = this.lineBuffer.indexOf("\n");
    }
    if (flushRemaining && this.lineBuffer.length > 0) {
      this.processLine(this.lineBuffer);
      this.lineBuffer = "";
    }
  }

  private processLine(rawLine: string): void {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (!line.startsWith("data:")) return;
    const payload = line.slice(5).trim();
    if (!payload || payload === "[DONE]") return;

    let parsed: unknown;
    try {
      parsed = JSON.parse(payload);
    } catch {
      return;
    }
    if (typeof parsed !== "object" || parsed === null) return;
    const obj = parsed as Record<string, unknown>;

    if (typeof obj.usage === "object" && obj.usage !== null) {
      const usage = obj.usage as Record<string, unknown>;
      if (typeof usage.prompt_tokens === "number" && Number.isFinite(usage.prompt_tokens)) {
        this.promptTokens = Math.max(0, Math.trunc(usage.prompt_tokens));
      }
      if (typeof usage.completion_tokens === "number" && Number.isFinite(usage.completion_tokens)) {
        this.completionTokens = Math.max(0, Math.trunc(usage.completion_tokens));
      }
    }

    if (Array.isArray(obj.choices)) {
      for (const choice of obj.choices) {
        if (typeof choice !== "object" || choice === null) continue;
        const delta = (choice as Record<string, unknown>).delta;
        if (typeof delta !== "object" || delta === null) continue;
        const deltaObj = delta as Record<string, unknown>;
        if (typeof deltaObj.content === "string") {
          this.textChars += deltaObj.content.length;
        }
        if (typeof deltaObj.reasoning_content === "string") {
          this.textChars += deltaObj.reasoning_content.length;
        }
      }
    }
  }
}

export function createSsePassthroughStream(
  onFinish: (usage: SseUsage) => Promise<void> | void,
): TransformStream<Uint8Array, Uint8Array> {
  const tracker = new SseUsageTracker();
  return new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, controller) {
      tracker.push(chunk);
      controller.enqueue(chunk);
    },
    async flush() {
      const usage = tracker.finish();
      try {
        await onFinish(usage);
      } catch {
        // Never fail stream completion if usage reporting throws.
      }
    },
  });
}
